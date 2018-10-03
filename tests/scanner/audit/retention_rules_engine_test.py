# Copyright 2018 The Forseti Security Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests the BigqueryRulesEngine."""

import copy
import itertools
import json
import mock
import unittest
import yaml

from tests.unittest_utils import ForsetiTestCase
from google.cloud.forseti.common.gcp_type import bigquery_access_controls as bq_acls
from google.cloud.forseti.common.gcp_type import organization
from google.cloud.forseti.common.gcp_type import project
from google.cloud.forseti.common.util import file_loader
from google.cloud.forseti.scanner.audit.errors import InvalidRulesSchemaError
from google.cloud.forseti.scanner.audit import base_rules_engine as bre
from google.cloud.forseti.scanner.audit import retention_rules_engine as rre
from google.cloud.forseti.scanner.audit import rules as scanner_rules
from tests.unittest_utils import get_datafile_path
from google.cloud.forseti.scanner.audit import errors as audit_errors


import collections
from tests.unittest_utils import ForsetiTestCase
from google.cloud.forseti.scanner.scanners import retention_scanner


def GetLefecycleDict(action, age, created_before, matches_storage_class, num_newer_versions, is_live):
    result = {'action':{}, 'condition':{}}
    result['action']['type'] = action
    if age != None:
        result['condition']['age'] = age
    if created_before != None:
        result['condition']['createdBefore'] = created_before
    if matches_storage_class != None:
        result['condition']['matchesStorageClass'] = matches_storage_class
    if num_newer_versions != None:
        result['condition']['numNewerVersions'] = num_newer_versions
    if is_live != None:
        result['condition']['isLive'] = is_live
    return result
    
def CreateFakeBucket(projectname, bucketname):
    name = projectname+bucketname
    full_name = 'organization/433655558669/project/'+projectname+'/bucket/'+name+'/'
    tp = 'bucket'
    parent_type_name = 'project/'+projectname
    data = '{"defaultObjectAcl": [{"entity": "project-owners-722028419187", "etag": "CAQ=", "kind": "storage#objectAccessControl", "projectTeam": {"projectNumber": "722028419187", "team": "owners"}, "role": "OWNER"}, {"entity": "project-editors-722028419187", "etag": "CAQ=", "kind": "storage#objectAccessControl", "projectTeam": {"projectNumber": "722028419187", "team": "editors"}, "role": "OWNER"}, {"entity": "project-viewers-722028419187", "etag": "CAQ=", "kind": "storage#objectAccessControl", "projectTeam": {"projectNumber": "722028419187", "team": "viewers"}, "role": "READER"}], "etag": "CAQ=", "id": "'+name+'", "kind": "storage#bucket", "lifecycle": {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 29, "createdBefore": "2018-08-15", "isLive": false, "matchesStorageClass": ["REGIONAL", "STANDARD", "DURABLE_REDUCED_AVAILABILITY", "NEARLINE", "COLDLINE"], "numNewerVersions": 17}}, {"action": {"type": "Delete"}, "condition": {"age": 37, "isLive": true}}]}, "location": "US-EAST1", "logging": {"logBucket": "audit-logs-'+projectname+'", "logObjectPrefix": "'+name+'"}, "metageneration": "4", "name": "'+name+'", "owner": {"entity": "project-owners-722028419187"}, "projectNumber": "722028419187", "selfLink": "https://www.googleapis.com/storage/v1/b/'+name+'", "storageClass": "REGIONAL", "timeCreated": "2018-09-13T18:45:14.101Z", "updated": "2018-09-26T13:38:28.286Z", "versioning": {"enabled": true}}'
    return (full_name, tp, parent_type_name, name, data)

def _mock_gcp_resource_iter(_, resource_type):
    """Creates a list of GCP resource mocks retrieved by the scanner."""
    resources = []
    if resource_type != 'bucket':
        raise ValueError(
            'unexpected resource type: got %s, bucket',
            resource_type,
        )

    Resource = collections.namedtuple(
        'Resource',
        # fields based on required fields from Resource in dao.py.
        ['full_name', 'type', 'name', 'parent_type_name', 'parent',
         'data'],
    )

    projectname = 'demo-project'
    name = projectname+'-test-bucket-1'
    full_name = 'organization/433655558669/project/'+projectname+'/bucket/'+name+'/'
    tp = 'bucket'
    parent_type_name = 'project/'+projectname
    data = '{"defaultObjectAcl": [{"entity": "project-owners-722028419187", "etag": "CAQ=", "kind": "storage#objectAccessControl", "projectTeam": {"projectNumber": "722028419187", "team": "owners"}, "role": "OWNER"}, {"entity": "project-editors-722028419187", "etag": "CAQ=", "kind": "storage#objectAccessControl", "projectTeam": {"projectNumber": "722028419187", "team": "editors"}, "role": "OWNER"}, {"entity": "project-viewers-722028419187", "etag": "CAQ=", "kind": "storage#objectAccessControl", "projectTeam": {"projectNumber": "722028419187", "team": "viewers"}, "role": "READER"}], "etag": "CAQ=", "id": "'+name+'", "kind": "storage#bucket", "lifecycle": {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 29, "createdBefore": "2018-08-15", "isLive": false, "matchesStorageClass": ["REGIONAL", "STANDARD", "DURABLE_REDUCED_AVAILABILITY", "NEARLINE", "COLDLINE"], "numNewerVersions": 17}}, {"action": {"type": "Delete"}, "condition": {"age": 37, "isLive": true}}]}, "location": "US-EAST1", "logging": {"logBucket": "audit-logs-'+projectname+'", "logObjectPrefix": "'+name+'"}, "metageneration": "4", "name": "'+name+'", "owner": {"entity": "project-owners-722028419187"}, "projectNumber": "722028419187", "selfLink": "https://www.googleapis.com/storage/v1/b/'+name+'", "storageClass": "REGIONAL", "timeCreated": "2018-09-13T18:45:14.101Z", "updated": "2018-09-26T13:38:28.286Z", "versioning": {"enabled": true}}'

    # bucket 1 with a smaller age
    bucket1 = CreateFakeBucket('demo-project', '-test-bucket-1')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 364, None, None, None, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 2 with a larger age
    bucket1 = CreateFakeBucket('demo-project', '-test-bucket-2')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 366, None, None, None, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 3 with created before
    bucket1 = CreateFakeBucket('demo-project', '-test-bucket-3')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, "2018-01-01", None, None, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 4 with matches Storage Class
    bucket1 = CreateFakeBucket('demo-project', '-test-bucket-4')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, ["REGIONAL", "STANDARD", "DURABLE_REDUCED_AVAILABILITY", "NEARLINE", "COLDLINE"], None, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 5 with numNewerVersions
    bucket1 = CreateFakeBucket('demo-project', '-test-bucket-5')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, 17, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 6 with isLive
    bucket1 = CreateFakeBucket('demo-project', '-test-bucket-6')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, True))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 7 with no "no match" violation
    bucket1 = CreateFakeBucket('demo-project', '-test-bucket-7')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, None))
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 201, None, None, None, True))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 11 with nothing
    bucket1 = CreateFakeBucket('correct-project', '-test-bucket-11')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 12 with created before
    bucket1 = CreateFakeBucket('correct-project', '-test-bucket-11')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, None))
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, "2018-01-01", None, None, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 13 with matches Storage Class
    bucket1 = CreateFakeBucket('correct-project', '-test-bucket-11')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, None))
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, ["REGIONAL", "STANDARD", "DURABLE_REDUCED_AVAILABILITY", "NEARLINE", "COLDLINE"], None, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 14 with numNewerVersions
    bucket1 = CreateFakeBucket('correct-project', '-test-bucket-11')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, None))
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, 17, None))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    # bucket 15 with is Live
    bucket1 = CreateFakeBucket('correct-project', '-test-bucket-11')
    datajson = json.loads(bucket1[4])
    datajson["lifecycle"]["rule"] = []
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, None))
    datajson["lifecycle"]["rule"].append(GetLefecycleDict("Delete", 365, None, None, None, True))
    resources.append(Resource(full_name=bucket1[0],type=bucket1[1],parent_type_name=bucket1[2],name=bucket1[3],parent=None,data=json.dumps(datajson)))

    return resources

# TODO: More tests need to be added that cover the rule attributes and how they
#    are evaluated
class BigqueryRulesEngineTest(ForsetiTestCase):
    """Tests for the BigqueryRulesEngine."""

    def setUp(self):
        """Set up."""

    def test_retention_retrieve_1(self):
        """No applies_to"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_1.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            self.assertEquals("Lack of applies_to in rule 0", str(e))

    def test_retention_retrieve_2(self):
        """applies_to is wrong"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_2.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "Miss dash (-) near applies_to in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_3(self):
        """Lack of min and max retention"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_3.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "Lack of minimum_retention and maximum_retention in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_4(self):
        """min larger than max"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_4.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "minimum_retention larger than maximum_retention in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_5(self):
        """Duplicate applies_to"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_5.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "Duplicate applies_to in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_11(self):
        """No resource"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_11.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "Lack of resource in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_12(self):
        """No resource type"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_12.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "Lack of type in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_13(self):
        """No resource ids"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_13.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "Lack of resource_ids in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_14(self):
        """resource_ids leaves empty"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_14.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = "Miss dash (-) near resource_ids in rule 0"
            self.assertEquals(expectErrStr, str(e))

    def test_retention_retrieve_21(self):
        """a more complex test case, should be all right"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_21.yaml')

        try:
            self.scanner = retention_scanner.RetentionScanner(
                {}, {}, mock.MagicMock(), '', '', rules_local_path)
        except audit_errors.InvalidRulesSchemaError as e:
            expectErrStr = ""
            self.assertEquals(expectErrStr, str(e))
        

    def test_find_bucket_retention_violations(self):
        """test_find_bucket_retention_violations"""

        rules_local_path = get_datafile_path(
            __file__,
            'bucket_retention_test_rules_0.yaml')
        self.scanner = retention_scanner.RetentionScanner(
            {}, {}, mock.MagicMock(), '', '', rules_local_path)

        mock_data_access = mock.MagicMock()
        mock_data_access.scanner_iter.side_effect = _mock_gcp_resource_iter
        mock_service_config = mock.MagicMock()
        mock_service_config.model_manager = mock.MagicMock()
        mock_service_config.model_manager.get.return_value = (
            mock.MagicMock(), mock_data_access)
        self.scanner.service_config = mock_service_config

        all_lifecycle_info = self.scanner._retrieve_bucket()
        all_violations = self.scanner._find_bucket_violations(all_lifecycle_info)

        expected_violations = set([
        rre.Rule.rttRuleViolation(
           resource_name="demo-project-test-bucket-1",
           resource_type="bucket",
           full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-1/",
           rule_name="exact retention 365",
           rule_index=0,
           violation_type='RETENTION_VIOLATION',
           violation_describe='age 364 is smaller than the minimum retention 365'),
       rre.Rule.rttRuleViolation(
           resource_name="demo-project-test-bucket-1",
           resource_type="bucket",
           full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-1/",
           rule_name="exact retention 365",
           rule_index=0,
           violation_type='RETENTION_VIOLATION',
           violation_describe='No condition satisfies the rule (min 365, max 365)'),
       rre.Rule.rttRuleViolation(
           resource_name="demo-project-test-bucket-2",
           resource_type="bucket",
           full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-2/",
           rule_name="exact retention 365",
           rule_index=0,
           violation_type='RETENTION_VIOLATION',
           violation_describe='age 366 is larger than the maximum retention 365'),
       rre.Rule.rttRuleViolation(
           resource_name="demo-project-test-bucket-2",
           resource_type="bucket",
           full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-2/",
           rule_name="exact retention 365",
           rule_index=0,
           violation_type='RETENTION_VIOLATION',
           violation_describe='No condition satisfies the rule (min 365, max 365)'),
       rre.Rule.rttRuleViolation(
           resource_name="demo-project-test-bucket-3",
           resource_type="bucket",
           full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-3/",
           rule_name="exact retention 365",
           rule_index=0,
           violation_type='RETENTION_VIOLATION',
           violation_describe='No condition satisfies the rule (min 365, max 365)'),
       rre.Rule.rttRuleViolation(
           resource_name="demo-project-test-bucket-4",
           resource_type="bucket",
           full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-4/",
           rule_name="exact retention 365",
           rule_index=0,
           violation_type='RETENTION_VIOLATION',
           violation_describe='No condition satisfies the rule (min 365, max 365)'),
       rre.Rule.rttRuleViolation(
           resource_name="demo-project-test-bucket-5",
           resource_type="bucket",
           full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-5/",
           rule_name="exact retention 365",
           rule_index=0,
           violation_type='RETENTION_VIOLATION',
           violation_describe='No condition satisfies the rule (min 365, max 365)'),
        rre.Rule.rttRuleViolation(
            resource_name="demo-project-test-bucket-6",
            resource_type="bucket",
            full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-6/",
            rule_name="exact retention 365",
            rule_index=0,
            violation_type='RETENTION_VIOLATION',
            violation_describe='No condition satisfies the rule (min 365, max 365)'),
        rre.Rule.rttRuleViolation(
            resource_name="demo-project-test-bucket-7",
            resource_type="bucket",
            full_name="organization/433655558669/project/demo-project/bucket/demo-project-test-bucket-7/",
            rule_name="exact retention 365",
            rule_index=0,
            violation_type='RETENTION_VIOLATION',
            violation_describe='age 201 is smaller than the minimum retention 365')
        ])

        self.assertEqual(expected_violations, set(all_violations))
        return



if __name__ == '__main__':
    unittest.main()