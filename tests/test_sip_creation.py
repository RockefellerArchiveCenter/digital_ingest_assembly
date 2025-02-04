from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from moto import mock_aws

from src.sip_creator import SIPCreator


class SIPCreatorTests(TestCase):

    @mock_aws
    def setUp(self):
        self.args = ['dev', 'us-east-1', 'package_id', 'src', 'tme', 'dest', 'https://zodiac.rockarch.org/api', '1a2b3c4d5e6f7g8h9i',
                     'arn:aws:iam::123456789012:role/digital-ingest-sns-role', 'topic', 'arn:aws:iam::123456789012:role/digital-ingest-ssm-role']
        self.sip_creator = SIPCreator(*self.args)

    @patch('src.sip_creator.SIPCreator.get_config')
    def test_init(self, mock_config):
        config = {}
        mock_config.return_value = config
        self.assertEqual(self.sip_creator.aws_region, self.args[1])
        self.assertEqual(self.sip_creator.package_id, self.args[2])
        self.assertEqual(self.sip_creator.tmp_dir, self.args[4])
        self.assertEqual(self.sip_creator.src_dir, self.args[3])
        self.assertEqual(self.sip_creator.dest_dir, self.args[5])
        self.assertEqual(self.sip_creator.service_name, "fornax")
        self.assertEqual(self.sip_creator.zodiac_baseurl, self.args[6])
        self.assertEqual(self.sip_creator.zodiac_api_key, self.args[7])
        self.assertEqual(self.sip_creator.sns_role_arn, self.args[8])
        self.assertEqual(self.sip_creator.sns_topic, self.args[9])
        self.assertEqual(self.sip_creator.ssm_role_arn, self.args[10])
        self.assertEqual(self.sip_creator.config, config)

    @patch('src.sip_creator.SIPCreator.send_failure_message')
    @patch('src.sip_creator.SIPCreator.cleanup_failed')
    @patch('src.sip_creator.SIPCreator.send_success_message')
    @patch('src.sip_creator.SIPCreator.cleanup_successful')
    @patch('src.sip_creator.SIPCreator.move_to_destination')
    @patch('src.sip_creator.SIPCreator.archive')
    @patch('src.sip_creator.SIPCreator.add_data')
    @patch('src.sip_creator.SIPCreator.restructure')
    @patch('src.sip_creator.SIPCreator.validate')
    @patch('src.sip_creator.SIPCreator.extract')
    @patch('src.sip_creator.SIPCreator.get_package_data')
    def test_run(
            self,
            mock_get_package,
            mock_extract,
            mock_validate,
            mock_restructure,
            mock_add_data,
            mock_archive,
            mock_move,
            mock_cleanup_successful,
            mock_success_message,
            mock_cleanup_failed,
            mock_failure_message):
        extracted_path = Path("foo")
        packaged_path = Path("bar")
        package_data = {}
        mock_extract.return_value = extracted_path
        mock_get_package.return_value = package_data
        mock_archive.return_value = packaged_path
        self.sip_creator.run()
        mock_get_package.assert_called_once()
        mock_extract.assert_called_once()
        self.assertEqual(mock_validate.call_count, 2)
        mock_validate.assert_called_with(extracted_path)
        mock_restructure.assert_called_once_with(extracted_path)
        mock_add_data.assert_called_once_with(extracted_path, package_data)
        mock_archive.assert_called_once_with(extracted_path)
        mock_move.assert_called_once_with(packaged_path)
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
        mock_init.return_value = None
        data = {}
        mock_data.return_value = data

        self.sip_creator.get_package_data()
        mock_init.assert_called_once_with(self.args[6], self.args[7])
        mock_data.assert_called_once_with(self.args[2])

    def test_extract(self):
        # set up binaries
        # assert unpacked
        # assert packed still exists
        pass

    def test_restructuring(self):
        # assert new dirs
        # Assert objects moved
        pass

    def test_add_data(self):
        # mock client calls, assert called_with
        # Assert bag-info
        pass

    def test_archive(self):
        # assert packaged file
        # assert not temp file
        pass

    def test_move_to_destination(self):
        # assert moved to dest
        # assert temp file rmoved
        pass

    def test_cleanup_successful(self):
        # assert source file rmoved
        pass

    def test_cleanup_failed(self):
        # assert destination removed
        # assert temp files (packaged and unpackged) removed
        pass
