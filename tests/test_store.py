import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from hotel.store import Store, RuleError, check_password, password_hash


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.store.bootstrap_admin('admin', 'passphrase-123')
        self.admin = {'id':1,'username':'admin','role':'admin'}
        self.store.create_user(self.admin, 'desk', 'passphrase-123', 'frontdesk')
        self.frontdesk = {'id':2,'username':'desk','role':'frontdesk'}
        self.room = self.store.add_room(self.admin, '101', 'Standard', 12000)
        self.guest = self.store.add_guest(self.admin, 'Test Guest', 'test@example.com')
        self.arrival = date.today().isoformat()
        self.departure = (date.today()+timedelta(days=2)).isoformat()

    def booking(self, **kwargs):
        data=dict(room_id=self.room,guest_id=self.guest,check_in=self.arrival,check_out=self.departure)
        data.update(kwargs)
        return self.store.create_reservation(self.admin, **data)

    def test_bootstrap_once(self):
        with self.assertRaises(RuleError):
            self.store.bootstrap_admin('other','passphrase-123')

    def test_hash_and_invalid_password(self):
        hashed=password_hash('passphrase-123')
        self.assertTrue(check_password('passphrase-123',hashed))
        self.assertFalse(check_password('wrong',hashed))
        with self.assertRaises(RuleError):password_hash('short')

    def test_sign_in_session_logout(self):
        token=self.store.login('ADMIN','passphrase-123')
        self.assertEqual(self.store.session_user(token)['role'],'admin')
        self.store.logout(token)
        self.assertIsNone(self.store.session_user(token))
        with self.assertRaises(RuleError):self.store.login('admin','bad')

    def test_admin_only_room_changes(self):
        with self.assertRaises(RuleError) as ctx:
            self.store.add_room(self.frontdesk,'102','Suite',25000)
        self.assertEqual(ctx.exception.status,403)
        with self.assertRaises(RuleError):
            self.store.set_room_state(self.frontdesk,self.room,'out_of_service')

    def test_guest_creation_allowed_to_frontdesk(self):
        guest=self.store.add_guest(self.frontdesk,'Second Guest')
        self.assertEqual(guest,2)

    def test_room_number_unique_case_insensitive(self):
        with self.assertRaises(RuleError) as ctx:
            self.store.add_room(self.admin,'101','Other',10000)
        self.assertEqual(ctx.exception.status,409)

    def test_invalid_dates(self):
        with self.assertRaises(RuleError):self.booking(check_in=self.departure,check_out=self.arrival)
        with self.assertRaises(RuleError):self.booking(check_in='2026-15-40')
        with self.assertRaises(RuleError):self.booking(check_in='2020-01-01',check_out='2020-01-04')

    def test_room_out_of_service_blocks_booking(self):
        self.store.set_room_state(self.admin,self.room,'out_of_service')
        with self.assertRaises(RuleError) as ctx:self.booking()
        self.assertEqual(ctx.exception.status,409)

    def test_book_and_overlap_rejected(self):
        first=self.booking()
        self.assertEqual(first,1)
        with self.assertRaises(RuleError) as ctx:self.booking()
        self.assertEqual(ctx.exception.status,409)
        self.assertEqual(len(self.store.reservations()),1)

    def test_adjacent_stays_allowed(self):
        self.booking()
        self.booking(check_in=self.departure,check_out=(date.today()+timedelta(days=4)).isoformat())
        self.assertEqual(len(self.store.reservations()),2)

    def test_availability_excludes_booked_room(self):
        self.store.add_room(self.admin,'102','Standard',9500)
        self.booking()
        self.assertEqual([r['number'] for r in self.store.available_rooms(self.arrival,self.departure)],['102'])

    def test_cancel_releases_availability(self):
        booking=self.booking()
        self.store.change_reservation(self.frontdesk,booking,'cancel')
        self.assertEqual(len(self.store.available_rooms(self.arrival,self.departure)),1)
        with self.assertRaises(RuleError):self.store.change_reservation(self.frontdesk,booking,'check_in')

    def test_check_in_and_out(self):
        booking=self.booking()
        self.store.change_reservation(self.frontdesk,booking,'check_in')
        self.assertEqual(self.store.reservations()[0]['status'],'checked_in')
        self.store.change_reservation(self.frontdesk,booking,'check_out')
        self.assertEqual(self.store.reservations()[0]['status'],'checked_out')
        with self.assertRaises(RuleError):self.store.change_reservation(self.frontdesk,booking,'check_out')

    def test_early_arrival_rejected(self):
        booking=self.booking(check_in=(date.today()+timedelta(days=1)).isoformat(),
                             check_out=(date.today()+timedelta(days=3)).isoformat())
        with self.assertRaises(RuleError):self.store.change_reservation(self.frontdesk,booking,'check_in')

    def test_occupied_room_cannot_go_out_of_service(self):
        booking=self.booking()
        self.store.change_reservation(self.frontdesk,booking,'check_in')
        with self.assertRaises(RuleError):self.store.set_room_state(self.admin,self.room,'out_of_service')

    def test_snapshot_rate_after_room_changes(self):
        booking=self.booking()
        with self.store.db(write=True) as db:
            db.execute('UPDATE rooms SET nightly_rate_cents=25000 WHERE id=?',(self.room,))
        self.assertEqual(self.store.reservations()[0]['rate_cents'],12000)

    def test_booking_concurrency_only_one_wins(self):
        barrier=threading.Barrier(4)
        results=[]
        def worker():
            barrier.wait()
            try:results.append(('ok',self.booking()))
            except RuleError as exc:results.append(('error',exc.status))
        threads=[threading.Thread(target=worker) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(x[0]=='ok' for x in results),1)
        self.assertEqual(len(self.store.reservations()),1)

    def test_activity_records_changes(self):
        self.booking()
        acts=[e['action'] for e in self.store.activity(self.admin)]
        self.assertIn('reservation_created',acts)
        with self.assertRaises(RuleError):self.store.activity(self.frontdesk)

    def test_negative_or_noninteger_rate_rejected(self):
        for bad in (-1,True,5.2,'10'):
            with self.subTest(bad=bad):
                with self.assertRaises(RuleError):self.store.add_room(self.admin,'302','Basic',bad)

    def test_staff_permission(self):
        with self.assertRaises(RuleError):self.store.create_user(self.frontdesk,'x','passphrase-123','admin')
        uid=self.store.create_user(self.admin,'third','passphrase-123','frontdesk')
        self.assertEqual(uid,3)
        self.assertEqual(len(self.store.users(self.admin)),3)
        with self.assertRaises(RuleError):self.store.users(self.frontdesk)

    def test_reservation_requires_guest(self):
        with self.assertRaises(RuleError) as ctx:self.booking(guest_id=999)
        self.assertEqual(ctx.exception.status,404)

    def test_date_overlap_end_exclusive(self):
        self.booking()
        result=self.store.available_rooms(self.departure,(date.today()+timedelta(days=3)).isoformat())
        self.assertEqual(len(result),1)

if __name__=='__main__':unittest.main()
