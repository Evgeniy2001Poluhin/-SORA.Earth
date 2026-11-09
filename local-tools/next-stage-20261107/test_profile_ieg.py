import unittest
from profile_ieg import profile,MAX_BYTES


class ProfileControls(unittest.TestCase):
    def test_duplicates_unrated_and_closed_stay_visible(self):
        result=profile(b'Project ID,Outcome\nP000001,Satisfactory\nP000001,Not Rated\nP000002,Closed\n,\n')
        self.assertEqual(result['profiled_rows'],4)
        self.assertEqual(result['distinct_nonempty_project_ids'],2)
        self.assertEqual(result['additional_duplicate_rows'],1)
        self.assertEqual(result['missing_project_ids'],1)
        self.assertEqual(result['rating_distribution'],{'missing':1,'not rated':1,'satisfactory':1,'unrecognized':1})
        self.assertFalse(result['independent_validation_ready'])
        self.assertFalse(result['training_performed'])

    def test_api_column_names_and_bom(self):
        result=profile('\ufeffproject_id,outcome\nP000003,Moderately Satisfactory\n'.encode())
        self.assertEqual(result['rating_distribution'],{'moderately satisfactory':1})

    def test_bad_inputs_are_refused(self):
        for raw in [b'',b'Project ID,Outcome\n',b'x,y\n1,2\n',b'Project ID,project_id,Outcome\na,b,c\n',
                    b'Project ID,Outcome\na,b,c\n',b'Project ID,Outcome\na\n',b'x'*(MAX_BYTES+1)]:
            with self.subTest(length=len(raw)):
                with self.assertRaises(ValueError):profile(raw)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProfileControls))
    if not result.wasSuccessful():raise SystemExit(1)
    print('IEG_PROFILE_CONTROLS_PASSED')
