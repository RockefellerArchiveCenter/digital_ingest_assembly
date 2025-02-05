import boto3
from amclient import AMClient, errors
from aws_assume_role_lib import assume_role
from requests import Session
from requests.exceptions import HTTPError

# TODO return dict from rights


class ArchivematicaClient():

    def __init__(self, am_api_key, am_user_name, am_url, transfer_source, processing_config):
        """Instantiates an Archivematica client.

        Args:
            am_api_key (str): API Key for Archivematica API
            am_user_name (str): username associated with the API key
            am_url (str): base URL for Archivematica instance
            transfer_source (str): Archivematica UUID for transfer source
            processing_config (str): name of processing config to include
        """
        self.client = AMClient(
            am_api_key=am_api_key,
            am_user_name=am_user_name,
            am_url=am_url,
            transfer_source=transfer_source,
            processing_config=processing_config)

    def get_processing_config(self):
        """Adds a processing configuration file from Archivematica to a package.

        Returns:
            str: processing configuration data.
        """
        processing_config = self.client.get_processing_config()
        if isinstance(processing_config, int):
            raise Exception(errors.error_lookup(processing_config), processing_config)
        return processing_config

    def get_rights_data(self, file_path_strings, rights_statements):
        """Gets structured rights information.

        Args:
            file_path_string (list of string): filepaths to include in rights data.
            rights_statements (list of dicts): rights statement data from Zodiac API

        Returns:
            list of dicts: rights data structured for Archivematica ingest
        """
        rights_data = []
        for file_string in file_path_strings:
            rights_rows = self.get_rights_rows(file_string, rights_statements)
            for rights_row in rights_rows:
                rights_data.append(rights_row)
        return rights_data

    def get_rights_rows(self, file_string, rights_statements):
        """Gets rows for each rights statement for a file.

        Args:
            file_string (str): filepath for file
            rights_statements (list of dicts): rights statement data from Zodiac API

        Returns:
            list of lists: rights rows as a list, with None values replaced by empty strings
        """
        rights_rows = []
        for rights_statement in rights_statements:
            rights_granted_rows = self.get_grant_restriction_rows(rights_statement['rights_granted'])
            for rights_granted_row in rights_granted_rows:
                rights_row = []
                rights_row.append(file_string)
                for basis_value in self.get_basis_fields(rights_statement):
                    rights_row.append(basis_value)
                rights_row[10:10] = rights_granted_row
                rights_rows.append(["" if c is None else c for c in rights_row])
        return rights_rows

    def get_basis_fields(self, rights_statement):
        """Gets values of rights basis fields.
        Checks for copyright status field to be represented by 'status' or 'copyright_status' key

        Args:
            rights_statement (dict): Single rights statement from Zodiac API

        Returns:
            list: rights basis fields.
        """
        copyright_status = ''
        if rights_statement.get('status'):
            copyright_status = rights_statement.get('status')
        elif rights_statement.get('copyright_status'):
            copyright_status = rights_statement.get('copyright_status')
        basis_note = rights_statement.get('basis_note') if rights_statement.get('basis_note') else rights_statement.get('note')
        basis_fields = [
            'rights_basis', 'determination_date', 'jurisdiction', 'start_date',
            'end_date', 'terms', 'citation', 'doc_id_type', 'doc_id_value',
            'doc_id_role']
        basis_values = [rights_statement.get(field) for field in basis_fields]
        basis_values.insert(7, basis_note)
        basis_values.insert(1, copyright_status)
        return basis_values

    def get_grant_restriction_rows(self, rights_granted_list):
        """
        Returns a row for each grant or restriction in a rights_granted list. If
        no grants or restrictions are present, returns one row with five empty strings.

        Checks for grant or restriction field to be represented by 'restriction' or 'grant_restriction' key

        Args:
            rights_granted_list (list of dicts): Rights granted from a rights statement in Zodiac API

        Returns:
            list: grant restriction fields
        """
        if not len(rights_granted_list):
            return [[''] * 5]
        rows = []
        for rights_granted in rights_granted_list:
            grant_restriction = rights_granted.get('restriction') if rights_granted.get('restriction') else rights_granted.get('grant_restriction')
            granted_note = rights_granted.get('granted_note') if rights_granted.get('granted_note') else rights_granted.get('note')
            rows.append([rights_granted['act'], grant_restriction, rights_granted.get('start_date'),
                         rights_granted.get('end_date'), granted_note])
        return rows

    def validate_rights_csv(self, csvfile):
        """Validates a rights CSV using Archivematica API.

        Args:
            csvfile (file object): CSV data to validate
        """
        result = self.client.validate_csv("rights", csvfile)
        if isinstance(result, int):
            message = getattr(result, "message", errors.error_lookup(result))
            raise Exception(f"Error validating CSV: {message}")


class AWSClient(object):

    def __init__(self, role_arn):
        """Gets Boto3 SNS client which authenticates with a specific IAM role."""
        session = boto3.Session()
        self.assumed_role_session = assume_role(session, role_arn)

    def get_client(self, resource, region_name):
        return self.assumed_role_session.client(resource, region_name=region_name)


class ZodiacClient(object):

    def __init__(self, baseurl, api_key):
        self.session = Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'X-Api-Key': api_key
        })
        self.baseurl = baseurl

    def get_package_data(self, package_id):
        url = f'{self.baseurl.rstrip()}/packages/{package_id}'
        try:
            resp = self.session.get(url)
            resp.raise_for_status()
            return resp.json()
        except HTTPError:
            raise Exception(f"Error fetching data for package {package_id}: {resp.status_code} {resp.text}")
