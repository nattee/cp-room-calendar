import json
import time
from datetime import date
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, quote, urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.core.cache import cache

from .google import CalendarError, EVENT_SCOPE, access_token, day_bounds, month_bounds, list_events, oauth, room_names
from .models import GoogleToken


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
})
class ViewerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='google:123', email='staff@cp.eng.chula.ac.th')
        self.room = dict(settings.ROOMS[0], name=None)
        cache.clear()

    def store(self, **extra):
        token = {'access_token': 'SECRET-ACCESS', 'refresh_token': 'SECRET-REFRESH', 'expires_at': time.time()+3600, 'scope': EVENT_SCOPE}
        token.update(extra)
        record, _ = GoogleToken.objects.get_or_create(user=self.user)
        record.write(token)
        return record

    def google_login_token(self, **claims):
        info = {'sub': 'new-subject', 'email': 'person@cp.eng.chula.ac.th', 'email_verified': True, 'hd': 'cp.eng.chula.ac.th'}
        info.update(claims)
        return {'userinfo': info, 'scope': f'openid email {EVENT_SCOPE}', 'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600}

    def test_anonymous_gets_signin_not_calendar_data(self):
        response = self.client.get('/')
        self.assertContains(response, 'Sign in with Google')
        self.assertNotContains(response, self.room['calendar_id'])
        response = self.client.get('/api/events', {'room': self.room['key'], 'date': '2026-09-10'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['Cache-Control'], 'no-store, private')

    def test_login_and_logout_require_post_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        for path in ('/auth/google/start', '/auth/logout'):
            self.assertEqual(client.get(path).status_code, 405)
            self.assertEqual(client.post(path).status_code, 403)

    def test_https_signin_with_real_csrf_cookie_and_origin(self):
        import re
        from django.http import HttpResponseRedirect
        client = Client(enforce_csrf_checks=True)
        host = 'room.cp.eng.chula.ac.th'
        response = client.get('/', secure=True, HTTP_HOST=host)
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode()).group(1)
        with patch.object(oauth.google, 'authorize_redirect', return_value=HttpResponseRedirect('https://accounts.google.com/')) as authorize:
            good = client.post('/auth/google/start', {'csrfmiddlewaretoken': token}, secure=True, HTTP_HOST=host, HTTP_ORIGIN=f'https://{host}')
            self.assertEqual(good.status_code, 302)
            authorize.assert_called_once()
            for origin in ('null', 'https://attacker.example'):
                bad = client.post('/auth/google/start', {'csrfmiddlewaretoken': token}, secure=True, HTTP_HOST=host, HTTP_ORIGIN=origin)
                self.assertEqual(bad.status_code, 403)
            authorize.assert_called_once()

    def test_https_post_referer_fallback_without_origin(self):
        import re
        from django.http import HttpResponseRedirect
        client = Client(enforce_csrf_checks=True)
        host = 'room.cp.eng.chula.ac.th'
        response = client.get('/', secure=True, HTTP_HOST=host)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode()).group(1)
        with patch.object(oauth.google, 'authorize_redirect', return_value=HttpResponseRedirect('https://accounts.google.com/')):
            good = client.post('/auth/google/start', {'csrfmiddlewaretoken': token}, secure=True, HTTP_HOST=host, HTTP_REFERER=f'https://{host}/')
            self.assertEqual(good.status_code, 302)
            bad = client.post('/auth/google/start', {'csrfmiddlewaretoken': token}, secure=True, HTTP_HOST=host)
            self.assertEqual(bad.status_code, 403)

    def test_callback_without_state_fails_closed(self):
        response = self.client.get('/auth/google/callback?code=forged&state=forged')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_google_authorization_uses_pkce_nonce_state_and_offline_access(self):
        with patch.object(oauth.google, 'load_server_metadata', return_value={
            'authorization_endpoint': 'https://accounts.google.com/o/oauth2/v2/auth',
        }):
            response = self.client.post('/auth/google/start')
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(response['Location']).query)
        self.assertEqual(query['redirect_uri'], [settings.PUBLIC_URL+'/auth/google/callback'])
        self.assertEqual(query['access_type'], ['offline'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertTrue(query['nonce'])
        self.assertTrue(query['state'])

    def test_only_verified_org_accounts_can_login(self):
        for change in ({'hd': 'gmail.com'}, {'email_verified': False}, {'email': 'person@evil.example'}, {'hd': None}, {'sub': ''}):
            with self.subTest(change=change), patch.object(oauth.google, 'authorize_access_token', return_value=self.google_login_token(**change)):
                self.assertEqual(self.client.get('/auth/google/callback').status_code, 403)
                self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_persists_session_and_encrypts_tokens(self):
        with patch.object(oauth.google, 'authorize_access_token', return_value=self.google_login_token()):
            response = self.client.get('/auth/google/callback')
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='google:new-subject')
        record = GoogleToken.objects.get(user=user)
        self.assertNotIn('new-refresh', record.encrypted)
        self.assertEqual(record.read()['refresh_token'], 'new-refresh')
        self.assertNotIn('userinfo', record.read())
        self.assertEqual(self.client.session.get_expiry_age(), 30*86400)
        self.assertFalse(user.has_usable_password())

    def test_missing_scope_and_missing_refresh_token_rejected(self):
        for field, value in (('scope', 'openid email'), ('refresh_token', '')):
            token = self.google_login_token(); token[field] = value
            with patch.object(oauth.google, 'authorize_access_token', return_value=token):
                self.assertEqual(self.client.get('/auth/google/callback').status_code, 403)
                self.assertNotIn('_auth_user_id', self.client.session)

    def test_signout_invalidates_session(self):
        self.client.force_login(self.user)
        self.client.post('/auth/logout')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_room_allowlist_and_date_validation(self):
        self.client.force_login(self.user)
        for query in ({'room': 'arbitrary@google.com', 'date': '2026-09-10'}, {'room': self.room['key'], 'date': 'bad'}, {'room': self.room['key'], 'date': '9999-01-01'}):
            self.assertEqual(self.client.get('/api/events', query).status_code, 400)

    def test_permissions_never_report_available(self):
        self.client.force_login(self.user)
        with patch('viewer.views.list_events', side_effect=CalendarError('No access', 403, 'calendar_access')):
            response = self.client.get('/api/events', {'room': self.room['key'], 'date': '2026-09-10'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('events', response.json())

    def test_bangkok_day_boundaries(self):
        start, end = day_bounds(date(2026,9,10))
        self.assertEqual(start.isoformat(), '2026-09-10T00:00:00+07:00')
        self.assertEqual(end.isoformat(), '2026-09-11T00:00:00+07:00')

    def test_month_bounds_include_full_weeks_and_leap_day(self):
        start, end = month_bounds(date(2024, 2, 14))
        self.assertEqual(start.isoformat(), '2024-01-29T00:00:00+07:00')
        self.assertEqual(end.isoformat(), '2024-03-04T00:00:00+07:00')
        start, end = month_bounds(date(2026, 12, 31))
        self.assertEqual(start.date(), date(2026, 11, 30))
        self.assertEqual(end.date(), date(2027, 1, 4))

    def test_month_api_uses_a_single_bounded_calendar_query(self):
        self.store()
        response = Mock(status_code=200)
        response.json.return_value = {'summary': 'Google room name', 'items': []}
        custom = dict(self.room, name='ห้องประชุม 705')
        with patch('viewer.google.requests.get', return_value=response) as get:
            result = list_events(self.user, custom, date(2024, 2, 14), view='month')
        self.assertEqual(result['room']['name'], 'ห้องประชุม 705')
        self.assertEqual(result['view'], 'month')
        self.assertEqual(result['month'], '2024-02')
        self.assertEqual(result['range_start'], '2024-01-29')
        self.assertEqual(result['range_end'], '2024-03-04')
        self.assertEqual(get.call_args.kwargs['params']['timeMin'], '2024-01-29T00:00:00+07:00')
        self.assertEqual(get.call_args.kwargs['params']['timeMax'], '2024-03-04T00:00:00+07:00')
        get.assert_called_once()

    def test_month_endpoint_and_invalid_view(self):
        self.client.force_login(self.user)
        with patch('viewer.views.list_events', return_value={'events': []}) as fetch:
            response = self.client.get('/api/events', {'room': self.room['key'], 'date': '2024-02-29', 'view': 'month'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(fetch.call_args.kwargs['view'], 'month')
        self.assertEqual(self.client.get('/api/events', {'room': self.room['key'], 'date': '2024-02-29', 'view': 'year'}).status_code, 400)

    def test_room_names_require_authentication(self):
        response = self.client.get('/api/rooms')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(self.room['calendar_id'], response.content.decode())

    def test_configured_names_do_not_need_google_and_stay_in_order(self):
        configured = [dict(self.room, name='ห้องประชุม 300'), dict(self.room, key='other', name='ห้องเรียน 248')]
        with override_settings(ROOMS=configured), patch('viewer.google.requests.get') as get:
            result = room_names(self.user)
        self.assertEqual([r['name'] for r in result], ['ห้องประชุม 300', 'ห้องเรียน 248'])
        get.assert_not_called()

    def test_google_names_are_cached_per_user_and_ids_not_returned(self):
        self.store()
        response = Mock(status_code=200)
        response.json.return_value = {'summary': 'Computer lab 610'}
        with override_settings(ROOMS=[self.room]), patch('viewer.google.requests.get', return_value=response) as get:
            self.assertEqual(room_names(self.user)[0]['name'], 'Computer lab 610')
            self.assertEqual(room_names(self.user)[0]['name'], 'Computer lab 610')
            self.assertEqual(get.call_count, 1)
            other = User.objects.create_user(username='google:room-names-other')
            record = GoogleToken(user=other)
            record.write({'access_token': 'OTHER-ACCESS', 'expires_at': time.time()+3600})
            result = room_names(other)
            self.assertEqual(get.call_count, 2)
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer OTHER-ACCESS')
            self.assertNotIn('calendar_id', result[0])
            self.assertEqual(get.call_args.kwargs['params']['fields'], 'summary')

    def test_inaccessible_calendar_name_does_not_hide_other_rooms(self):
        self.store()
        rooms = [self.room, dict(self.room, key='custom', name='My custom room')]
        with override_settings(ROOMS=rooms), patch('viewer.google.requests.get', return_value=Mock(status_code=403)):
            names = room_names(self.user)
        self.assertFalse(names[0]['resolved'])
        self.assertEqual(names[1]['name'], 'My custom room')

    def test_valid_token_does_not_refresh(self):
        self.store()
        with patch('viewer.google.requests.post') as post:
            self.assertEqual(access_token(self.user), 'SECRET-ACCESS')
        post.assert_not_called()

    def test_refresh_preserves_refresh_token_when_google_omits_it(self):
        self.store(expires_at=0)
        response = Mock(status_code=200)
        response.json.return_value = {'access_token': 'renewed', 'expires_in': 3600}
        with patch('viewer.google.requests.post', return_value=response):
            self.assertEqual(access_token(self.user), 'renewed')
        saved = GoogleToken.objects.get(user=self.user).read()
        self.assertEqual(saved['refresh_token'], 'SECRET-REFRESH')
        self.assertGreater(saved['expires_at'], time.time())

    def test_revoked_refresh_token_requires_reconnect(self):
        self.store(expires_at=0)
        with patch('viewer.google.requests.post', return_value=Mock(status_code=400)):
            with self.assertRaises(CalendarError) as caught:
                access_token(self.user)
        self.assertEqual(caught.exception.code, 'reconnect')

    def test_pagination_all_day_transparent_and_cancelled_events(self):
        self.store()
        a = {'id':'1','summary':'<script>bad</script>','start':{'date':'2026-09-10'},'end':{'date':'2026-09-11'},'transparency':'transparent'}
        b = {'id':'2','start':{'dateTime':'2026-09-10T10:00:00+07:00'},'end':{'dateTime':'2026-09-10T11:00:00+07:00'}}
        first = Mock(status_code=200); first.json.return_value={'summary':'Room 300','items':[a,{'status':'cancelled'}],'nextPageToken':'page-two'}
        second = Mock(status_code=200); second.json.return_value={'items':[b]}
        with patch('viewer.google.requests.get', side_effect=[first,second]) as get:
            data = list_events(self.user,self.room,date(2026,9,10))
        self.assertEqual(len(data['events']),2)
        self.assertEqual(data['room']['name'],'Room 300')
        self.assertFalse(data['events'][0]['busy'])
        self.assertEqual(data['events'][1]['title'],'ไม่ว่าง / Busy')
        self.assertIn('%40resource.calendar.google.com',get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs['params']['pageToken'],'page-two')

    def test_upstream_permission_error_not_empty_result(self):
        self.store()
        for status in (403,404,429,500):
            with self.subTest(status=status), patch('viewer.google.requests.get',return_value=Mock(status_code=status)):
                with self.assertRaises(CalendarError):
                    list_events(self.user,self.room,date(2026,9,10))

    def test_different_users_use_their_own_tokens(self):
        self.store()
        other = User.objects.create_user(username='google:other')
        with self.assertRaises(CalendarError) as caught:
            access_token(other)
        self.assertEqual(caught.exception.code,'reconnect')

    def test_authenticated_page_has_navigation_and_no_credentials(self):
        self.client.force_login(self.user)
        response = self.client.get('/')
        self.assertContains(response, 'next-room')
        self.assertContains(response, 'prev-room')
        self.assertNotContains(response, settings.GOOGLE_CREDENTIALS['client_secret'])
        self.assertNotContains(response, self.room['calendar_id'])
        self.assertIn("frame-ancestors 'none'",response['Content-Security-Policy'])

    def role_response(self, role, **extra):
        response = Mock(status_code=200)
        response.json.return_value = {'accessRole': role, **extra}
        return response

    def test_booking_link_needs_signin(self):
        response = self.client.get('/book', {'room': self.room['key'], 'date': '2026-09-18', 'start': '13:00'})
        self.assertRedirects(response, '/')
        self.assertNotIn(self.room['calendar_id'], response['Location'])

    def test_booking_rejects_unknown_room_and_invalid_date_or_time(self):
        self.store(); self.client.force_login(self.user)
        with override_settings(ROOMS=[self.room]), patch('viewer.google.requests.get') as get:
            for params in ({'room': 'no-such-room', 'date': '2026-09-18', 'start': '13:00'},
                           {'room': self.room['key'], 'date': '1999-12-31', 'start': '13:00'},
                           {'room': self.room['key'], 'date': 'someday', 'start': '13:00'},
                           {'room': self.room['key'], 'date': '2026-09-18', 'start': 'lunchtime'}):
                self.assertEqual(self.client.get('/book', params).status_code, 400)
            get.assert_not_called()

    def test_booking_invites_the_room_for_one_hour_in_bangkok(self):
        self.store(); self.client.force_login(self.user)
        with override_settings(ROOMS=[self.room]), patch('viewer.google.requests.get', return_value=self.role_response('writer')):
            response = self.client.get('/book', {'room': self.room['key'], 'date': '2026-09-18', 'start': '13:00'})
        self.assertEqual(response.status_code, 302)
        target = urlparse(response['Location'])
        query = parse_qs(target.query)
        self.assertEqual(target.netloc + target.path, 'calendar.google.com/calendar/render')
        self.assertEqual(query['action'], ['TEMPLATE'])
        self.assertEqual(query['src'], [self.room['calendar_id']])
        self.assertNotIn('add', query)
        self.assertEqual(query['ctz'], ['Asia/Bangkok'])
        self.assertEqual(query['dates'], ['20260918T130000/20260918T140000'])

    def test_booking_refused_for_staff_who_only_read_the_room(self):
        self.store(); self.client.force_login(self.user)
        for role in ('reader', 'freeBusyReader', 'none'):
            cache.clear()
            with override_settings(ROOMS=[self.room]), patch('viewer.google.requests.get', return_value=self.role_response(role)):
                response = self.client.get('/book', {'room': self.room['key'], 'date': '2026-09-18', 'start': '13:00'})
            self.assertEqual(response.status_code, 403, role)
            self.assertNotContains(response, self.room['calendar_id'], status_code=403)

    def test_events_payload_reports_booking_rights(self):
        self.store()
        for role, permitted in (('owner', True), ('writer', True), ('reader', False),
                                ('freeBusyReader', False), (None, False)):
            cache.clear()
            with override_settings(ROOMS=[self.room]), patch('viewer.google.requests.get',
                                                             return_value=self.role_response(role, items=[])):
                payload = list_events(self.user, self.room, date(2026, 9, 18))
            self.assertEqual(payload['can_book'], permitted, role)

    def day_response(self, items=(), role='reader'):
        response = Mock(status_code=200)
        response.json.return_value = {'accessRole': role, 'summary': 'Meeting room', 'items': list(items)}
        return response

    def second_room(self):
        return dict(self.room, key='second-room', name='ห้องที่สอง',
                    calendar_id='second-room@resource.calendar.google.com')

    def test_all_rooms_day_requires_signin(self):
        response = self.client.get('/api/day', {'date': '2026-09-18'})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(self.room['calendar_id'], response.content.decode())

    def test_all_rooms_day_validates_the_date(self):
        self.store(); self.client.force_login(self.user)
        with patch('viewer.google.requests.get') as get:
            for value in ('', 'someday', '1999-12-31', '2101-01-01'):
                self.assertEqual(self.client.get('/api/day', {'date': value}).status_code, 400, value)
            get.assert_not_called()

    def test_all_rooms_day_covers_every_room_in_order_without_ids(self):
        self.store(); self.client.force_login(self.user)
        second = self.second_room()
        event = {'id': 'e1', 'summary': 'ประชุมภาควิชา',
                 'start': {'dateTime': '2026-09-18T09:00:00+07:00'},
                 'end': {'dateTime': '2026-09-18T10:00:00+07:00'}}
        with override_settings(ROOMS=[self.room, second]), \
                patch('viewer.google.requests.get', return_value=self.day_response([event])) as get:
            response = self.client.get('/api/day', {'date': '2026-09-18'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([r['key'] for r in data['rooms']], [self.room['key'], 'second-room'])
        self.assertEqual(data['rooms'][0]['events'][0]['title'], 'ประชุมภาควิชา')
        self.assertEqual(data['date'], '2026-09-18')
        self.assertEqual(get.call_count, 2)
        body = response.content.decode()
        self.assertNotIn(self.room['calendar_id'], body)
        self.assertNotIn(second['calendar_id'], body)

    def test_one_unreadable_room_does_not_hide_the_others(self):
        self.store(); self.client.force_login(self.user)
        second = self.second_room()

        def answer(url, **kwargs):
            return self.day_response() if quote(self.room['calendar_id'], safe='') in url else Mock(status_code=403)

        with override_settings(ROOMS=[self.room, second]), \
                patch('viewer.google.requests.get', side_effect=answer):
            data = self.client.get('/api/day', {'date': '2026-09-18'}).json()
        self.assertNotIn('error', data['rooms'][0])
        self.assertEqual(data['rooms'][1]['error'], 'calendar_access')
        self.assertEqual(data['rooms'][1]['events'], [])
        self.assertEqual(data['rooms'][1]['name'], 'ห้องที่สอง')

    def test_all_rooms_day_keeps_bangkok_bounds(self):
        self.store(); self.client.force_login(self.user)
        with override_settings(ROOMS=[self.room]), \
                patch('viewer.google.requests.get', return_value=self.day_response()) as get:
            self.client.get('/api/day', {'date': '2026-09-18'})
        params = get.call_args.kwargs['params']
        self.assertEqual(params['timeMin'], '2026-09-18T00:00:00+07:00')
        self.assertEqual(params['timeMax'], '2026-09-19T00:00:00+07:00')
