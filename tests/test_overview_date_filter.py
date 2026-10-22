"""No network or app startup required for Overview date range tests."""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from leadfinder.overview_filters import date_window, filter_leads_by_created_at


class OverviewDateFilterTests(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame([
            {"id": 1, "created_at": "2026-10-21T22:59:59+00:00"},  # 23:59:59 Casablanca
            {"id": 2, "created_at": "2026-10-21T23:00:00+00:00"},  # 00:00:00 Casablanca on Oct 22
            {"id": 3, "created_at": "2026-10-22T22:59:59+00:00"},  # 23:59:59 Casablanca on Oct 22
            {"id": 4, "created_at": "2026-10-22T23:00:00+00:00"},  # 00:00:00 Casablanca on Oct 23
            {"id": 5, "created_at": None},
        ])

    def test_all_time_keeps_every_lead_including_missing_created_at(self):
        r=filter_leads_by_created_at(self.rows,None,None,'Africa/Casablanca')
        self.assertEqual(list(r.id),[1,2,3,4,5])

    def test_selected_day_end_inclusive_using_local_calendar(self):
        r=filter_leads_by_created_at(self.rows,date(2026,10,22),date(2026,10,22),'Africa/Casablanca')
        self.assertEqual(list(r.id),[2,3])

    def test_open_ended_start_and_end(self):
        self.assertEqual(list(filter_leads_by_created_at(self.rows,date(2026,10,23),None,'Africa/Casablanca').id),[4])
        self.assertEqual(list(filter_leads_by_created_at(self.rows,None,date(2026,10,21),'Africa/Casablanca').id),[1])

    def test_invalid_range_is_rejected(self):
        with self.assertRaises(ValueError): date_window(date(2026,10,23),date(2026,10,21),'Africa/Casablanca')

    def test_epoch_window(self):
        low, high = date_window(date(2026,10,22),date(2026,10,22),'Africa/Casablanca')
        self.assertEqual(datetime.fromtimestamp(low,timezone.utc).hour,23)
        self.assertEqual(high-low,24*3600)


if __name__=='__main__':unittest.main()
