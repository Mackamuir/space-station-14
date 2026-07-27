import unittest

import partition_tests


class PartitionTestsTest(unittest.TestCase):
    def test_groups_all_test_cases_from_a_fixture_file_together(self):
        tests = [
            "Content.IntegrationTests.Tests.PrototypeSaveTest.UninitializedSaveTest",
            "Content.IntegrationTests.Tests.PrototypeSaveTest.CreateSaveLoadSaveGrid",
            "Content.IntegrationTests.Tests.PrototypeSaveTest.LoadSaveTicksSave(1)",
            "Content.IntegrationTests.Tests.OtherTest.UnrelatedTest",
        ]

        test_files = partition_tests.extract_test_files(tests)

        self.assertEqual(
            test_files,
            {
                "Content.IntegrationTests.Tests.PrototypeSaveTest": tests[:3],
                "Content.IntegrationTests.Tests.OtherTest": tests[3:],
            },
        )
        self.assertEqual(
            partition_tests.build_filter(test_files),
            "class=='Content.IntegrationTests.Tests.OtherTest'||"
            "class=='Content.IntegrationTests.Tests.PrototypeSaveTest'",
        )


if __name__ == "__main__":
    unittest.main()
