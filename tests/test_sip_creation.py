import tarfile
from pathlib import Path
from shutil import copy, rmtree
from unittest import TestCase
from unittest.mock import patch

import bagit
from moto import mock_aws

from src.sip_creator import SIPCreator


class SIPCreatorTests(TestCase):

    @mock_aws
    def setUp(self):
        self.fixture_path = Path('tests', 'fixtures')
        self.package_id = '0edb4066-980c-491f-bd73-c80a6546ff6d'
        self.src_dir = 'source_dir'
        self.tmp_dir = 'temp_dir'
        self.dest_dir = 'dest_dir'
        self.args = ['dev', 'us-east-1', self.package_id, self.src_dir, self.tmp_dir, self.dest_dir,
                     'arn:aws:iam::123456789012:role/digital-ingest-sns-role', 'topic',
                     'arn:aws:iam::123456789012:role/digital-ingest-ssm-role']
        self.sip_creator = SIPCreator(*self.args)
        for dir in [self.src_dir, self.tmp_dir, self.dest_dir]:
            Path(dir).mkdir()

    def copy_extracted(self, target_path):
        current_path = Path(self.fixture_path, 'bags', f"{self.package_id}.tar.gz")
        with tarfile.open(current_path, "r:*") as tf:
            tf.extractall(target_path)

    @patch('src.sip_creator.SIPCreator.get_config')
    def test_init(self, mock_config):
        config = {}
        mock_config.return_value = config
        self.assertEqual(self.sip_creator.aws_region, self.args[1])
        self.assertEqual(self.sip_creator.package_id, self.package_id)
        self.assertEqual(self.sip_creator.tmp_dir, self.tmp_dir)
        self.assertEqual(self.sip_creator.src_dir, self.src_dir)
        self.assertEqual(self.sip_creator.dest_dir, self.dest_dir)
        self.assertEqual(self.sip_creator.service_name, "digital_ingest_assembly")
        self.assertEqual(self.sip_creator.sns_role_arn, self.args[6])
        self.assertEqual(self.sip_creator.sns_topic, self.args[7])
        self.assertEqual(self.sip_creator.ssm_role_arn, self.args[8])
        self.assertEqual(self.sip_creator.config, config)

    @patch('src.sip_creator.SIPCreator.send_failure_message')
    @patch('src.sip_creator.SIPCreator.cleanup_failed')
    @patch('src.sip_creator.SIPCreator.send_success_message')
    @patch('src.sip_creator.SIPCreator.cleanup_successful')
    @patch('src.sip_creator.SIPCreator.archive')
    @patch('src.sip_creator.SIPCreator.add_data')
    @patch('src.sip_creator.SIPCreator.restructure')
    @patch('src.sip_creator.SIPCreator.validate')
    @patch('src.sip_creator.SIPCreator.extract')
    @patch('src.sip_creator.SIPCreator.get_package_data')
    @patch('src.sip_creator.SIPCreator.send_start_message')
    def test_run(
            self,
            mock_start_message,
            mock_get_package,
            mock_extract,
            mock_validate,
            mock_restructure,
            mock_add_data,
            mock_archive,
            mock_cleanup_successful,
            mock_success_message,
            mock_cleanup_failed,
            mock_failure_message):
        """Assert that all methods are called with correct args."""
        extracted_path = Path("foo")
        packaged_path = Path("bar")
        package_data = {}
        mock_extract.return_value = extracted_path
        mock_get_package.return_value = package_data
        mock_archive.return_value = packaged_path
        mock_add_data.return_value = package_data

        self.sip_creator.run()

        mock_start_message.assert_called_once()
        mock_get_package.assert_called_once()
        mock_extract.assert_called_once()
        self.assertEqual(mock_validate.call_count, 2)
        mock_validate.assert_called_with(extracted_path)
        mock_restructure.assert_called_once_with(extracted_path)
        mock_add_data.assert_called_once_with(extracted_path, package_data)
        mock_archive.assert_called_once_with(extracted_path)
        mock_cleanup_successful.assert_called_once_with()
        mock_success_message.assert_called_once_with(package_data)
        mock_cleanup_failed.assert_not_called()
        mock_failure_message.assert_not_called()

        exception = Exception("foo")
        mock_get_package.side_effect = exception
        self.sip_creator.run()
        mock_cleanup_failed.assert_called_once()
        mock_failure_message.assert_called_once_with(exception)

    @patch('src.clients.ZodiacClient.__init__')
    @patch('src.clients.ZodiacClient.get_package_data')
    def test_get_package_data(self, mock_data, mock_init):
        """Asserts that package data is fetched with correct args."""
        mock_init.return_value = None
        data = {}
        mock_data.return_value = data
        baseurl = "https://zodiac.rockarch.org"
        self.sip_creator.config = {"ZODIAC_BASEURL": baseurl}

        self.sip_creator.get_package_data()
        mock_init.assert_called_once_with(baseurl)
        mock_data.assert_called_once_with(self.package_id)

    def test_extract(self):
        """Asserts extract results in expected files and dirs."""
        fixture_path = self.fixture_path / 'bags' / f'{self.package_id}.tar.gz'
        src_path = Path(self.src_dir, f'{self.package_id}.tar.gz')
        copy(fixture_path, src_path)

        self.sip_creator.extract()

        self.assertTrue(Path(self.tmp_dir, self.package_id).is_dir())
        self.assertTrue(src_path.is_file())

    def test_restructuring(self):
        """Assert package is restructured correctly."""
        self.copy_extracted(self.src_dir)
        package_path = Path(self.src_dir, self.package_id)

        self.sip_creator.restructure(package_path)

        for dir in ['objects', 'logs', 'metadata', 'metadata/submissionDocumentation']:
            self.assertTrue((package_path / 'data' / dir).is_dir())
        self.assertTrue((package_path / 'data' / 'objects' / 'metadata.json').is_file())

    @patch('src.clients.ArchivematicaClient.__init__')
    @patch('src.clients.ArchivematicaClient.get_rights_data')
    @patch('src.clients.ArchivematicaClient.get_processing_config')
    @patch('src.clients.ArchivematicaClient.validate_rights_csv')
    def test_add_data(self, mock_validate, mock_processing_config, mock_data, mock_init):
        mock_init.return_value = None
        mock_data.return_value = [{'grant_act': 'publish',
                                   'grant_restriction': 'disallow',
                                   'grant_start_date': '1911-06-12',
                                   'grant_end_date': '',
                                   'grant_note': '',
                                   'file': 'data/objects/foo.txt',
                                   'basis': 'copyright',
                                   'status': 'public domain',
                                   'determination_date': '2021-08-02',
                                   'jurisdiction': 'us',
                                   'start_date': '2031-06-12',
                                   'end_date': '',
                                   'terms': '',
                                   'citation': '',
                                   'note': 'Copyright term has expired.',
                                   'doc_id_type': '',
                                   'doc_id_value': '',
                                   'doc_id_role': ''}]
        mock_processing_config.return_value = "<processingMCP><preconfiguredChoices></preconfiguredChoices></processingMCP>"
        mock_validate.return_value = {"valid": "true"}
        package_path = Path(self.tmp_dir, self.package_id)
        self.copy_extracted(self.tmp_dir)
        (package_path / 'data' / 'objects').mkdir()
        (package_path / 'data' / 'objects' / 'example.txt').touch()
        package_data = {"origin": "aurora", "rights_statements": [{"foo": "bar"}]}
        expected = {
            'origin': 'aurora',
            'rights_statements': [{'foo': 'bar'}],
            'identifiers': {
                'archivesspace_archival_object': '/repositories/2/archival_objects/1'}}
        self.sip_creator.config = {
            "AURORA_AM_API_KEY": "api key",
            "AURORA_AM_USER_NAME": "user name",
            "AURORA_AM_URL": "url",
            "AURORA_TRANSFER_SOURCE": "transfer source",
            "AURORA_PROCESSING_CONFIG": "processing config"
        }

        output = self.sip_creator.add_data(package_path, package_data)

        self.assertEqual(output, expected)
        mock_init.assert_called_once_with(
            am_api_key='api key',
            am_user_name='user name',
            am_url='url',
            transfer_source='transfer source',
            processing_config='processing config')
        mock_data.assert_called_once_with(['data/objects/example.txt'], [{"foo": "bar"}])
        self.assertTrue((package_path / 'data' / 'metadata' / 'rights.csv').is_file())
        self.assertTrue((package_path / 'processingMCP.xml').is_file())
        bag = bagit.Bag(str(package_path))
        self.assertTrue(bag.is_valid())

    def test_archive(self):
        """Asserts package is archived to correct location"""
        package_path = Path(self.tmp_dir, self.package_id)
        self.copy_extracted(self.tmp_dir)

        self.sip_creator.archive(package_path)

        self.assertTrue(Path(self.tmp_dir, f'{self.package_id}.tar.gz').is_file())
        self.assertFalse(package_path.exists())

    def test_cleanup_successful(self):
        """Asserts package is cleaned up after success."""
        source_path = Path(self.src_dir, f"{self.package_id}.tar.gz")
        source_path.touch()

        self.sip_creator.cleanup_successful()

        self.assertFalse(source_path.exists())

    def test_cleanup_failed(self):
        """Asserts package is cleaned up after failure"""
        package_name = f"{self.package_id}.tar.gz"
        Path(self.tmp_dir, self.package_id).mkdir(parents=True)
        Path(self.tmp_dir, package_name).touch()

        self.sip_creator.cleanup_failed()

        for path in [
                Path(self.tmp_dir, package_name),
                Path(self.tmp_dir, self.package_id)]:
            self.assertFalse(path.exists())

    def tearDown(self):
        for dir in [self.src_dir, self.tmp_dir, self.dest_dir]:
            rmtree(dir)
