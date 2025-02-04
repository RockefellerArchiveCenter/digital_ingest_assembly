import csv
import tarfile
import traceback
from pathlib import Path
from shutil import rmtree

import bagit

from .clients import ArchivematicaClient, AWSClient, ZodiacClient

# TODO consider things that need to be triggered on a cron (start package, clean up dashboard) - what's the best path forward?
# TODO implement logging
# TODO specify args and returns in docstrings


class SIPMaker(object):

    def __init__(self, environment, aws_region, package_id, src_dir, tmp_dir, dest_dir, zodiac_baseurl, zodiac_api_key, sns_role_arn, sns_topic, ssm_role_arn):
        self.aws_region = aws_region
        self.package_id = package_id
        self.tmp_dir = tmp_dir
        self.src_dir = src_dir
        self.dest_dir = dest_dir
        self.service_name = "fornax"
        self.zodiac_baseurl = zodiac_baseurl
        self.zodiac_api_key = zodiac_api_key
        self.sns_role_arn = sns_role_arn
        self.sns_topic = sns_topic
        self.ssm_role_arn = ssm_role_arn
        self.config = self.get_config(environment)

    def run(self):
        """Main class method which calls other service logic."""
        try:
            package_data = self.get_package_data()
            extracted_path = self.extract()
            self.validate(extracted_path)
            self.restructure(extracted_path)
            self.add_data(extracted_path, package_data)
            self.validate(extracted_path)
            packaged_path = self.archive(extracted_path)
            self.move_to_destination(packaged_path)
            self.cleanup_successful()
            self.send_success_message(package_data)
        except Exception as e:
            self.cleanup_failed()
            self.send_failure_message(e)

    def get_config(self, environment):
        """Fetch config values from Parameter Store.

        Args:
            ssm_parameter_path (str): Path to parameters

        Returns:
            configuration (dict): all parameters found at the supplied path.
        """
        ssm_parameter_path = f"/{environment}/fornax"
        configuration = {}
        ssm_client = AWSClient(self.ssm_role_arn).get_client('ssm', self.aws_region)
        try:
            param_details = ssm_client.get_parameters_by_path(
                Path=ssm_parameter_path,
                Recursive=False,
                WithDecryption=True)

            for param in param_details.get('Parameters', []):
                param_path_array = param.get('Name').split("/")
                section_position = len(param_path_array) - 1
                section_name = param_path_array[section_position]
                configuration[section_name] = param.get('Value')

        except BaseException:
            print("Encountered an error loading config from SSM.")
            traceback.print_exc()
        finally:
            return configuration

    def get_package_data(self):
        """Fetches data from Zodiac API."""
        zodiac_client = ZodiacClient(self.zodiac_baseurl, self.zodiac_api_key)
        return zodiac_client.get_package_data(self.package_id)

    def extract(self):
        """Extracts compressed TAR file to temporary directory."""
        current_path = Path(self.src_dir, f"{self.package_id}.tar.gz")
        with tarfile.open(current_path, "r:*") as tf:
            tf.extractall(self.tmp_dir)
        return Path(self.tmp_dir, self.package_id)

    def validate(self, extracted_path):
        """Validates package against BagIt specification."""
        bag = bagit.Bag(str(extracted_path))
        bag.validate()

    def restructure(self, extracted_path):
        """Creates Archivematica-compliant directory structure"""
        data_path = Path(extracted_path, 'data')
        objects_path = data_path / 'objects'
        log_path = data_path / 'logs'
        metadata_path = data_path / 'metadata'
        docs_path = metadata_path / 'submissionDocumentation'
        for p in [objects_path, log_path, docs_path]:
            if p.is_dir():
                p.mkdir(parents=True)
        for f in data_path.iterdir():
            if f.is_file():
                f.rename(objects_path / f.name)

    def add_data(self, extracted_path, package_data):
        """Adds rights CSV, processing config, and data to bag-info.txt"""
        am_client = ArchivematicaClient(package_data['origin'])

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
                csvwriter = csv.writer(csvfile)
                csvwriter.writerow(rights_csv_field_names)
                csvwriter.writerows(rights_data)
            self.validate_rights_csv(csvfile)

        processing_config = am_client.get_processing_config()
        with open(extracted_path / 'processingMCP.xml', 'w') as f:
            f.write(processing_config)

        bag = bagit.Bag(extracted_path)
        bag.info['Internal-Sender-Identifier'] = self.package_id
        bag.save(manifests=True)

    def archive(self, extracted_path):
        """Creates a compressed TAR file from a package."""
        tar_path = extracted_path / f'{self.package_id}.tar.gz'
        with tarfile.open(tar_path, "w:gz", compresslevel=1) as tar:
            tar.add(extracted_path, arcname=extracted_path.name)
        rmtree(extracted_path)
        return tar_path

    def move_to_destination(self, packaged_path):
        """Moves archived package to destination directory."""
        destination_path = Path(self.dest_dir, f"{self.package_id}.tar.gz")
        packaged_path.rename(destination_path)

    def cleanup_successful(self):
        """Removes file from source directory."""
        Path(self.src_dir, f"{self.package_id}.tar.gz").unlink()

    def send_success_message(self, package_data):
        """Sends success message to SNS topic."""
        client = AWSClient(self.sns_role_arn).get_client('sns', self.aws_region)
        client.publish(
            TopicArn=self.sns_topic,
            Message=f'SIP for package {self.package_id} successfully created',
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
                'package_data': {
                    'DataType': 'String',
                    'StringValue': package_data
                }
            })

    def cleanup_failed(self):
        """Removes temporary and destination files if they exist."""
        package_name = f"{self.package_id}.tar.gz"
        Path(self.dest_dir, package_name).unlink(missing_ok=True)
        Path(self.tmp_dir, package_name).unlink(missing_ok=True)
        if Path(self.tmp_dir, self.package_id).is_dir():
            rmtree(Path(self.tmp_dir, self.package_id))

    def send_failure_message(self, exception):
        """Sends failure message to SNS topic"""
        client = AWSClient(self.role_arn).get_client('sns', self.aws_region)
        tb = ''.join(traceback.format_exception(exception)[:-1])
        client.publish(
            TopicArn=self.sns_topic,
            Message=f'SIP creation for package {self.package_id} failed',
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
                    'StringValue': f'{str(exception)}\n\n<pre>{tb}</pre>',
                }
            })


if __name__ == '__main__':
    # TODO get env variables, pass as args
    SIPMaker().run()
