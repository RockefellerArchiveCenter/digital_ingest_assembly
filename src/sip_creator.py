import csv
import logging
import tarfile
import traceback
from os import getenv
from pathlib import Path
from shutil import rmtree

import bagit

from src.clients import ArchivematicaClient, AWSClient, ZodiacClient

logging.basicConfig(
    level=int(getenv('LOGGING_LEVEL', logging.INFO)),
    format='%(filename)s::%(funcName)s::%(lineno)s %(message)s')
logging.getLogger("bagit").setLevel(logging.ERROR)


class SIPCreator(object):

    def __init__(self,
                 environment,
                 aws_region,
                 package_id,
                 src_dir,
                 tmp_dir,
                 dest_dir,
                 sns_role_arn,
                 sns_topic,
                 ssm_role_arn):
        self.aws_region = aws_region
        self.package_id = package_id
        self.tmp_dir = tmp_dir
        self.src_dir = src_dir
        self.dest_dir = dest_dir
        self.service_name = "digital_ingest_assembly"
        self.sns_role_arn = sns_role_arn
        self.sns_topic = sns_topic
        self.ssm_role_arn = ssm_role_arn
        self.config = self.get_config(environment)

    def run(self):
        """Main class method which calls other service logic."""
        try:
            self.send_start_message()
            package_data = self.get_package_data()
            extracted_path = self.extract()
            self.validate(extracted_path)
            self.restructure(extracted_path)
            updated_package = self.add_data(extracted_path, package_data)
            self.validate(extracted_path)
            self.archive(extracted_path)
            self.cleanup_successful()
            self.send_success_message(updated_package)
            logging.info(
                f'Package {self.package_id} prepared for Archivematica ingest.')
        except Exception as e:
            logging.error(e)
            self.cleanup_failed()
            self.send_failure_message(e)

    def get_config(self, environment):
        """Fetch config values from Parameter Store.

        Args:
            ssm_parameter_path (str): Path to parameters

        Returns:
            configuration (dict): all parameters found at the supplied path.
        """
        ssm_parameter_path = f"/{environment}/digital_ingest_assembly"
        configuration = {}
        ssm_client = AWSClient(self.ssm_role_arn).get_client('ssm', self.aws_region)
        try:
            paginator = ssm_client.get_paginator('get_parameters_by_path')
            response_iterator = paginator.paginate(Path=ssm_parameter_path)
            for page in response_iterator:
                for entry in page['Parameters']:
                    param_path_array = entry.get('Name').split("/")
                    section_position = len(param_path_array) - 1
                    section_name = param_path_array[section_position]
                    configuration[section_name] = entry.get('Value')
        except BaseException:
            logging.error("Encountered an error loading config from SSM.")
            traceback.print_exc()
        finally:
            return configuration

    def send_start_message(self):
        client = AWSClient(self.sns_role_arn).get_client('sns', self.aws_region)
        client.publish(
            TopicArn=self.sns_topic,
            Message=f'Assembly for {self.package_id} started.',
            MessageAttributes={
                'package_id': {
                    'DataType': 'String',
                    'StringValue': self.package_id,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'STARTED',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': f'Assembly for {self.package_id} started.',
                }
            })
        logging.debug('Start notification delivered.')

    def get_package_data(self):
        """Fetches data from Zodiac API.

        Returns:
            dict: package data from Zodiac API
        """
        zodiac_client = ZodiacClient(self.config['ZODIAC_BASEURL'])
        data = zodiac_client.get_package_data(self.package_id)
        logging.debug(f'Data for {self.package_id} fetched: {data}')
        return data

    def extract(self):
        """Extracts compressed TAR file to temporary directory.

        Returns:
            pathlib.Path: path to extracted package
        """
        current_path = Path(self.src_dir, f"{self.package_id}.tar.gz")
        unpacked_path = Path(self.tmp_dir, self.package_id)
        with tarfile.open(current_path, "r:*") as tf:
            tf.extractall(self.tmp_dir)
        logging.debug(f'Package {self.package_id} unpacked to {unpacked_path}')
        return unpacked_path

    def validate(self, extracted_path):
        """Validates package against BagIt specification.

        Args:
            extracted_path (pathlib.Path): path to package
        """
        bag = bagit.Bag(str(extracted_path))
        bag.validate()
        logging.debug(f'Package {self.package_id} is a valid bag')

    def restructure(self, extracted_path):
        """Creates Archivematica-compliant directory structure

        Args:
            extracted_path (pathlib.Path): path to package
        """
        data_path = extracted_path / 'data'
        objects_path = data_path / 'objects'
        log_path = data_path / 'logs'
        metadata_path = data_path / 'metadata'
        docs_path = metadata_path / 'submissionDocumentation'
        for p in [objects_path, log_path, docs_path]:
            p.mkdir(parents=True)
        for f in data_path.rglob('*'):
            if f.is_file():
                f.rename(objects_path / f.name)
        logging.debug(f'Package {self.package_id} restructured')

    def add_data(self, extracted_path, package_data):
        """Adds rights CSV, processing config, and data to bag-info.txt

        Args:
            extracted_path (pathlib.Path): path to package
            package_data (dict): data about package

        Returns:
            dict: updated package data
        """
        origin = package_data['origin'].upper()
        am_client = ArchivematicaClient(
            am_api_key=self.config[f'{origin}_AM_API_KEY'],
            am_user_name=self.config[f'{origin}_AM_USER_NAME'],
            am_url=self.config[f'{origin}_AM_URL'],
            transfer_source=self.config[f'{origin}_TRANSFER_SOURCE'],
            processing_config=self.config[f'{origin}_PROCESSING_CONFIG'])

        if package_data.get('rights_statements'):
            rights_csv_field_names = [
                'file', 'basis', 'status', 'determination_date', 'jurisdiction',
                'start_date', 'end_date', 'terms', 'citation', 'note', 'grant_act',
                'grant_restriction', 'grant_start_date', 'grant_end_date',
                'grant_note', 'doc_id_type', 'doc_id_value', 'doc_id_role']
            file_names = [str(f).replace(str(extracted_path), '').lstrip('/') for f in (extracted_path / 'data' / 'objects').rglob('*')]
            rights_data = am_client.get_rights_data(
                file_names,
                package_data['rights_statements'])
            csv_filepath = extracted_path / 'data' / 'metadata' / 'rights.csv'
            csv_filepath.parent.mkdir(exist_ok=True)
            with open(csv_filepath, 'w') as csvfile:
                dictwriter = csv.DictWriter(csvfile, fieldnames=rights_csv_field_names)
                dictwriter.writeheader()
                dictwriter.writerows(rights_data)
            with open(csv_filepath, 'r') as csvfile:
                am_client.validate_rights_csv(csvfile)
            logging.debug(f'Rights CSV added to package {self.package_id}')

        processing_config = am_client.get_processing_config()
        with open(extracted_path / 'processingMCP.xml', 'w') as f:
            f.write(processing_config)
        logging.debug(f'Processing config added to package {self.package_id}')

        bag = bagit.Bag(str(extracted_path))
        archivesspace_uri = bag.info.get('ArchivesSpace-URI')
        if archivesspace_uri:
            package_data.setdefault('identifiers', {}).update({'archivesspace_archival_object': archivesspace_uri})
        bag.save(manifests=True)
        logging.debug(f'bag-info.txt for package {self.package_id} updated')
        return package_data

    def archive(self, extracted_path):
        """Creates a compressed TAR file from a package.

        Args:
            extracted_path (pathlib.Path): path to package
        """
        tar_path = Path(self.tmp_dir, f'{self.package_id}.tar.gz')
        with tarfile.open(tar_path, "w:gz", compresslevel=1) as tar:
            tar.add(extracted_path, arcname=extracted_path.name)
        rmtree(extracted_path)
        logging.debug(f'Archive file created for package {self.package_id} at {tar_path}')

    def cleanup_successful(self):
        """Removes file from source directory."""
        Path(self.src_dir, f"{self.package_id}.tar.gz").unlink()
        logging.debug(f'Cleanup from sucessful job complete for {self.package_id}')

    def send_success_message(self, package_data):
        """Sends success message to SNS topic.

        Args:
            package_data (dict): data about package
        """
        client = AWSClient(self.sns_role_arn).get_client('sns', self.aws_region)
        client.publish(
            TopicArn=self.sns_topic,
            Message=package_data,
            MessageAttributes={
                'package_id': {
                    'DataType': 'String',
                    'StringValue': self.package_id,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'SUCCESS',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': f'SIP for package {self.package_id} successfully created'
                }
            })
        logging.debug(f'Success message sent for {self.package_id}')

    def cleanup_failed(self):
        """Removes temporary and destination files if they exist."""
        package_name = f"{self.package_id}.tar.gz"
        Path(self.tmp_dir, package_name).unlink(missing_ok=True)
        if Path(self.tmp_dir, self.package_id).is_dir():
            rmtree(Path(self.tmp_dir, self.package_id))
        logging.debug(f'Cleanup from failed job complete for {self.package_id}')

    def send_failure_message(self, exception):
        """Sends failure message to SNS topic.

        Args:
            exception (Exception): the error that was thrown.
        """
        client = AWSClient(self.sns_role_arn).get_client('sns', self.aws_region)
        tb = ''.join(traceback.format_exception(exception)[:-1])
        client.publish(
            TopicArn=self.sns_topic,
            Message=tb,
            MessageAttributes={
                'package_id': {
                    'DataType': 'String',
                    'StringValue': self.package_id,
                },
                'service': {
                    'DataType': 'String',
                    'StringValue': self.service_name,
                },
                'outcome': {
                    'DataType': 'String',
                    'StringValue': 'FAILURE',
                },
                'message': {
                    'DataType': 'String',
                    'StringValue': str(exception),
                }
            })
        logging.debug(f'Failure message sent for {self.package_id}')


if __name__ == '__main__':
    environment = getenv('ENVIRONMENT')
    aws_region = getenv('AWS_REGION')
    package_id = getenv('PACKAGE_ID')
    src_dir = getenv('SRC_DIR')
    tmp_dir = getenv('TMP_DIR')
    dest_dir = getenv('DEST_DIR')
    sns_role_arn = getenv('SNS_ROLE_ARN')
    sns_topic = getenv('SNS_TOPIC')
    ssm_role_arn = getenv('SSM_ROLE_ARN')
    SIPCreator(
        environment,
        aws_region,
        package_id,
        src_dir,
        tmp_dir,
        dest_dir,
        sns_role_arn,
        sns_topic,
        ssm_role_arn
    ).run()
