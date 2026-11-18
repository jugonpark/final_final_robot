import unittest
from python_controller.models import PersonObservation
from python_controller.vision.person_tracker import TargetSelector
from python_controller.vision.gesture_recognizer import associate_hand, choose_gesture


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.people = [PersonObservation(1,(20,20,220,440),.9,1), PersonObservation(2,(300,20,500,440),.9,1)]

    def test_single_candidate_and_multiple_click_selection(self):
        s = TargetSelector()
        self.assertEqual(s.candidate(self.people[:1]),1)
        self.assertIsNone(s.candidate(self.people))
        self.assertEqual(s.at_point(self.people,350,100),2)
        self.assertIsNone(s.at_point(self.people,250,100))

    def test_hand_unique_owner_and_overlap_rejected(self):
        points = [(100,100)]*21
        self.assertEqual(associate_hand(points,self.people),1)
        overlap = self.people + [PersonObservation(3,(80,80,250,450),.9,1)]
        self.assertIsNone(associate_hand(points,overlap))
        self.assertIsNone(associate_hand([(600,100)]*21,self.people))

    def test_target_only_stop_priority_and_conflict(self):
        raw = [('Open_Palm',.99,1),('Closed_Fist',.95,1),('Closed_Fist',.99,2)]
        self.assertEqual(choose_gesture(raw,1,1).name,'Closed_Fist')
        self.assertIsNone(choose_gesture([('Thumb_Up',.9,1),('Thumb_Down',.9,1)],1,1))
        self.assertIsNone(choose_gesture([('Open_Palm',.9,2)],1,1))

    def test_multi_person_gestures_cannot_grant_control(self):
        self.assertIsNone(choose_gesture([('Open_Palm',.99,1)],1,1,people_count=2))
