import json
from pathlib import Path
from shutil import copytree, rmtree
from unittest import TestCase
from unittest.mock import patch

import botocore
from amclient import AMClient, errors, utils
from boto3 import Session as BotoSession
from moto import mock_aws
from requests import Session as RequestsSession
from requests.exceptions import HTTPError

from src.clients import ArchivematicaClient, AWSClient, ZodiacClient


class ArchivematicaClientTests(TestCase):

    def setUp(self):
        self.tmp_path = Path('tmp')
        self.fixture_path = Path('tests', 'fixtures', 'csv_creation')
        if self.tmp_path.is_dir():
            rmtree(self.tmp_path)
        self.tmp_path.mkdir()
        for directory in ['aurora_example', 'digitization_example']:
            copytree(self.fixture_path / directory, self.tmp_path / directory)
        self.args = [
            'am_api_key',
            'am_user_name',
            'am_url',
            'transfer_source',
            'processing_config']
        self.client = ArchivematicaClient(*self.args)

    def test_init(self):
        """Assert attributes are correctly set on init"""
        self.assertIsInstance(self.client.client, AMClient)
        self.assertEqual(self.client.client.am_api_key, self.args[0])
        self.assertEqual(self.client.client.am_user_name, self.args[1])
        self.assertEqual(self.client.client.am_url, self.args[2])
        self.assertEqual(self.client.client.transfer_source, self.args[3])
        self.assertEqual(self.client.client.processing_config, self.args[4])

    @patch('amclient.AMClient.get_processing_config')
    def test_get_processing_config(self, mock_processing_config):
        file_content = "<processingMCP><preconfiguredChoices></preconfiguredChoices></processingMCP>"
        mock_processing_config.return_value = file_content
        output = self.client.get_processing_config()
        self.assertEqual(output, file_content)

        mock_processing_config.return_value = 1
        with self.assertRaises(Exception):
            self.client.add_processing_config('aurora_example')

    @patch('amclient.AMClient.validate_csv')
    @patch('src.clients.ArchivematicaClient.get_rights_rows')
    def test_get_rights_data(self, mock_rights_rows, mock_validate):
        mock_validate.return_value = {"valid": "true"}
        rights_row = [
            'data/objects/foo.txt',
            'rights_basis',
            'status',
            'determination_date',
            'jurisdiction',
            'start_date',
            'end_date',
            'terms',
            'citation',
            'note',
            'publish',
            'disallow',
            '2020-01-01',
            '',
            'granted note',
            'doc_id_type',
            'doc_id_value',
            'doc_id_role']
        mock_rights_rows.return_value = [rights_row]
        with open(self.fixture_path / 'aurora_example.json', 'r') as json_file:
            json_data = json.load(json_file)
        output = self.client.get_rights_data(
            ["data/objects/sample.txt"],
            json_data["bag_data"]["rights_statements"])
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0], rights_row)

    @patch('src.clients.ArchivematicaClient.get_grant_restriction_rows')
    @patch('src.clients.ArchivematicaClient.get_basis_fields')
    def test_get_rights_rows(self, mock_basis, mock_grant):
        mock_basis.return_value = ['rights_basis', 'status', 'determination_date', 'jurisdiction', 'start_date',
                                   'end_date', 'terms', 'citation', 'note', 'doc_id_type', 'doc_id_value',
                                   'doc_id_role']
        mock_grant.return_value = [
            ['publish', 'disallow', '2020-01-01', '', 'granted note'],
            ['disseminate', 'allow', '2020-01-01', '', 'granted note']
        ]
        with open(self.fixture_path / 'rights_statements.json') as df:
            rights_statements = json.load(df)
            output = self.client.get_rights_rows("data/objects/foo.txt", rights_statements)
            self.assertEqual(len(output), 4)
            self.assertEqual(output[0],
                             ['data/objects/foo.txt',
                              'rights_basis',
                              'status',
                              'determination_date',
                              'jurisdiction',
                              'start_date',
                              'end_date',
                              'terms',
                              'citation',
                              'note',
                              'publish',
                              'disallow',
                              '2020-01-01',
                              '',
                              'granted note',
                              'doc_id_type',
                              'doc_id_value',
                              'doc_id_role'])
            self.assertEqual(output[1],
                             ['data/objects/foo.txt',
                              'rights_basis',
                              'status',
                              'determination_date',
                              'jurisdiction',
                              'start_date',
                              'end_date',
                              'terms',
                              'citation',
                              'note',
                              'disseminate',
                              'allow',
                              '2020-01-01',
                              '',
                              'granted note',
                              'doc_id_type',
                              'doc_id_value',
                              'doc_id_role'])

    def test_get_grant_restriction_note(self):
        output = self.client.get_grant_restriction_rows([])
        self.assertEqual(output, [['', '', '', '', '']])

        grants_rows = [
            {
                "act": "publish",
                "grant_restriction": "disallow",
                "start_date": "1911-06-12",
                "end_date": None,
                "granted_note": None
            },
            {
                "act": "publish",
                "restriction": "disallow",
                "start_date": "1911-06-12",
                "end_date": None,
                "note": None
            }

        ]
        expected_row = ['publish', 'disallow', '1911-06-12', None, None]
        output = self.client.get_grant_restriction_rows(grants_rows)
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0], expected_row)
        self.assertEqual(output[1], expected_row)

    def test_get_basis_fields(self):
        expected_row = ['copyright', 'public domain', '2021-08-02', 'us', '2031-06-12', None, None, None, 'Copyright term has expired.', None, None, None]

        for rights_statement in [
                {
                    "rights_basis": "copyright",
                    "start_date": "2031-06-12",
                    "end_date": None,
                    "basis_note": "Copyright term has expired.",
                    "rights_granted": [],
                    "determination_date": "2021-08-02",
                    "jurisdiction": "us",
                    "copyright_status": "public domain"
                },
                {
                    "rights_basis": "copyright",
                    "start_date": "2031-06-12",
                    "end_date": None,
                    "note": "Copyright term has expired.",
                    "rights_granted": [],
                    "determination_date": "2021-08-02",
                    "jurisdiction": "us",
                    "status": "public domain"
                }]:
            output = self.client.get_basis_fields(rights_statement)
            self.assertEqual(len(output), 12)
            self.assertEqual(output, expected_row)

    @patch('amclient.AMClient.validate_csv')
    def test_validate_csv(self, mock_validate):
        """Asserts CSV files are validated as expected"""
        with open(self.fixture_path / 'aurora_example.json', 'r') as json_file:
            mock_validate.return_value = {"valid": "true"}
            self.client.validate_rights_csv(json_file)

            json_file.seek(0)

            message = "error message for invalid CSV."
            mock_validate.return_value = utils.Error(errors.ERR_INVALID_RESPONSE, message=message)
            with self.assertRaises(Exception) as err:
                self.client.validate_rights_csv(json_file)
            self.assertIn(message, str(err.exception))

    def tearDown(self):
        if self.tmp_path.is_dir():
            rmtree(self.tmp_path)


class AWSClientTests(TestCase):

    @mock_aws
    def test_init(self):
        """Asserts attributes are correctly set on init"""
        role_arn = 'arn:aws:iam::123456789012:role/digital-ingest-role'
        client = AWSClient(role_arn)
        self.assertIsInstance(client.assumed_role_session, BotoSession)

    @mock_aws
    def test_get_client(self):
        """Asserts function returns correct client"""
        role_arn = 'arn:aws:iam::123456789012:role/digital-ingest-role'
        client = AWSClient(role_arn).get_client('sns', 'us-east-1')
        self.assertIsInstance(client, botocore.client.BaseClient)
        self.assertEqual(client.meta.service_model.service_name, 'sns')


class ZodiacClientTests(TestCase):

    def setUp(self):
        self.baseurl = 'https://example.com'
        self.api_key = '123456789'
        self.client = ZodiacClient(self.baseurl, self.api_key)

    def test_init(self):
        """Asserts attributes are correctly set on init"""
        self.assertIsInstance(self.client.session, RequestsSession)
        self.assertEqual(self.client.session.headers.get('X-Api-Key'), self.api_key)
        self.assertEqual(self.client.session.headers.get('Accept'), 'application/json')
        self.assertEqual(self.client.baseurl, self.baseurl)

    @patch('requests.Session.get')
    def test_package_data(self, mock_get):
        """Assert get requests and exceptions are handled as expected"""
        data = {}
        mock_get.return_value.json.return_value = data
        mock_get.return_value.raise_for_status.return_value = None
        package_id = '12345'
        output = self.client.get_package_data(package_id)
        self.assertEqual(output, data)
        mock_get.assert_called_once_with(f'{self.baseurl}/packages/{package_id}')

        mock_get.return_value.raise_for_status.side_effect = HTTPError('foo')
        with self.assertRaises(Exception) as err:
            self.client.get_package_data(package_id)
        self.assertTrue(str(err.exception).startswith('Error fetching data for package 12345'))
