import time
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as day_time, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import requests
from authlib.integrations.django_client import OAuth
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core.cache import cache

from .models import GoogleToken

EVENT_SCOPE = 'https://www.googleapis.com/auth/calendar.events.readonly'
# Google reports the signed-in person's access level on each room calendar.
# Booking here goes through the booking group, who hold 'Make changes to events'
# on the room calendars. Staff who can only read a room are not offered the button.
BOOKING_ROLES = ('writer', 'owner')
CREATE_EVENT_URL = 'https://calendar.google.com/calendar/render'
oauth = OAuth()
oauth.register(
    'google',
    client_id=settings.GOOGLE_CREDENTIALS['client_id'],
    client_secret=settings.GOOGLE_CREDENTIALS['client_secret'],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': f'openid email {EVENT_SCOPE}',
        'code_challenge_method': 'S256',
        'timeout': 15,
    },
)


class CalendarError(Exception):
    def __init__(self, message, status=502, code='upstream_error'):
        self.message, self.status, self.code = message, status, code
        super().__init__(message)


def reconnect():
    return CalendarError('กรุณาเชื่อมต่อ Google อีกครั้ง / Please reconnect Google.', 401, 'reconnect')


def access_token(user, force=False):
    try:
        record = GoogleToken.objects.get(user=user)
        token = record.read()
    except (GoogleToken.DoesNotExist, InvalidToken):
        raise reconnect()
    if not force and token.get('expires_at', 0) > time.time() + 90:
        return token['access_token']
    if not token.get('refresh_token'):
        raise reconnect()
    try:
        response = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': settings.GOOGLE_CREDENTIALS['client_id'],
            'client_secret': settings.GOOGLE_CREDENTIALS['client_secret'],
            'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'],
        }, timeout=15)
    except requests.RequestException:
        raise CalendarError('เชื่อมต่อ Google ไม่ได้ชั่วคราว / Google is temporarily unreachable.')
    if response.status_code in (400, 401):
        raise reconnect()
    if response.status_code != 200:
        raise CalendarError('Google ไม่พร้อมใช้งาน กรุณาลองใหม่ / Please try again shortly.', 503)
    data = response.json()
    token.update(data)
    token['expires_at'] = time.time() + int(data.get('expires_in', 3600))
    record.write(token)
    return token['access_token']


def day_bounds(date):
    start = datetime.combine(date, day_time.min, ZoneInfo(settings.TIME_ZONE))
    return start, start + timedelta(days=1)


def month_bounds(date):
    first = date.replace(day=1)
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    # Include the leading/trailing dates in the Monday-first month grid.
    grid_start = first - timedelta(days=first.weekday())
    grid_end = following + timedelta(days=(-following.weekday()) % 7)
    return day_bounds(grid_start)[0], day_bounds(grid_end)[0]


def _room_digest(room):
    return hashlib.sha256(room['calendar_id'].encode()).hexdigest()


def _role_key(user, room):
    return f'room-role:{user.pk}:{_room_digest(room)}'


def room_names(user):
    """Resolve Google names using the existing event-read scope; never return IDs."""
    pending = [r for r in settings.ROOMS if not r.get('name')]
    token = access_token(user) if pending else None

    def resolve(room):
        if room.get('name'):
            return {'key': room['key'], 'name': room['name']}
        digest = _room_digest(room)
        cache_key = f'room-name:{user.pk}:{digest}'
        name = cache.get(cache_key)
        if not name:
            url = 'https://www.googleapis.com/calendar/v3/calendars/' + quote(room['calendar_id'], safe='') + '/events'
            try:
                response = requests.get(url, params={'fields': 'summary', 'maxResults': 1},
                                        headers={'Authorization': f'Bearer {token}'}, timeout=10)
                if response.status_code == 200:
                    name = response.json().get('summary')
                    if name:
                        cache.set(cache_key, name, 600)
            except (requests.RequestException, ValueError):
                pass
        return {'key': room['key'], 'name': name or f"ชื่อห้องไม่พร้อมใช้งาน ({room['key']})", 'resolved': bool(name)}

    # No database access inside worker threads; use the user's token acquired above.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(resolve, settings.ROOMS))


def _fetch_events(token, room, start, end, renew=None):
    """One room's bookings between two moments, following Google's paging.

    `renew` supplies a fresh access token after a 401. The all-rooms view leaves it out:
    its workers run in threads, which must not touch the database.
    """
    params = {
        'timeMin': start.isoformat(), 'timeMax': end.isoformat(),
        'singleEvents': 'true', 'orderBy': 'startTime', 'maxResults': 250,
        'timeZone': settings.TIME_ZONE,
        'fields': 'summary,accessRole,nextPageToken,items(id,summary,start,end,status,transparency)',
    }
    url = 'https://www.googleapis.com/calendar/v3/calendars/' + quote(room['calendar_id'], safe='') + '/events'
    result = []
    name = room.get('name') or room['key']
    role = None
    for page in range(20):
        try:
            response = requests.get(url, params=params, headers={'Authorization': f'Bearer {token}'}, timeout=15)
            if response.status_code == 401 and renew:
                token = renew()
                response = requests.get(url, params=params, headers={'Authorization': f'Bearer {token}'}, timeout=15)
        except requests.RequestException:
            raise CalendarError('เชื่อมต่อ Google ไม่ได้ชั่วคราว / Google is temporarily unreachable.')
        if response.status_code == 401:
            raise reconnect()
        if response.status_code in (403, 404):
            # Do not label permission failures as an empty/free room.
            raise CalendarError('อ่านปฏิทินนี้ไม่ได้ โปรดตรวจสอบสิทธิ์และการเปิดใช้ Calendar API / Calendar access unavailable. Check sharing permissions and API access.', 403, 'calendar_access')
        if response.status_code == 429:
            raise CalendarError('Google จำกัดการเรียกข้อมูลชั่วคราว กรุณารอสักครู่ / Please wait before refreshing.', 429, 'rate_limit')
        if response.status_code != 200:
            raise CalendarError('โหลดปฏิทินไม่สำเร็จ กรุณาลองใหม่ / Could not load the calendar.')
        data = response.json()
        role = data.get('accessRole') or role
        name = room.get('name') or data.get('summary') or name
        for event in data.get('items', []):
            if event.get('status') == 'cancelled':
                continue
            result.append({
                'id': event['id'], 'title': event.get('summary') or 'ไม่ว่าง / Busy',
                'start': event['start'], 'end': event['end'],
                'busy': event.get('transparency') != 'transparent',
            })
        if not data.get('nextPageToken'):
            return {'name': name, 'role': role, 'events': result}
        params['pageToken'] = data['nextPageToken']
    raise CalendarError('มีรายการมากเกินไปสำหรับช่วงวันที่นี้ / Too many events to show this date range safely.', 503)


def list_events(user, room, date, view='day'):
    start, end = month_bounds(date) if view == 'month' else day_bounds(date)
    token = access_token(user)
    fetched = _fetch_events(token, room, start, end, renew=lambda: access_token(user, force=True))
    if fetched['role']:
        cache.set(_role_key(user, room), fetched['role'], 600)
    return {'room': {'key': room['key'], 'name': fetched['name']}, 'date': date.isoformat(),
            'view': view, 'month': date.strftime('%Y-%m'),
            'range_start': start.date().isoformat(), 'range_end': end.date().isoformat(),
            'timezone': settings.TIME_ZONE, 'events': fetched['events'],
            'can_book': fetched['role'] in BOOKING_ROLES,
            'fetched_at': datetime.now(ZoneInfo(settings.TIME_ZONE)).isoformat()}


def list_day(user, day):
    """One day across every configured room. One room's failure never hides the others."""
    start, end = day_bounds(day)
    token = access_token(user)

    def one(room):
        try:
            fetched = _fetch_events(token, room, start, end)
        except CalendarError as exc:
            return {'key': room['key'], 'name': room.get('name') or room['key'],
                    'events': [], 'error': exc.code, 'message': exc.message}
        if fetched['role']:
            cache.set(_role_key(user, room), fetched['role'], 600)
        return {'key': room['key'], 'name': fetched['name'], 'events': fetched['events']}

    # No database access inside worker threads; the token above is already renewed.
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(one, settings.ROOMS))
    return {'date': day.isoformat(), 'timezone': settings.TIME_ZONE, 'rooms': entries,
            'fetched_at': datetime.now(ZoneInfo(settings.TIME_ZONE)).isoformat()}


def may_book(user, room):
    """Whether Google's access level for this person on this room allows inviting it."""
    role = cache.get(_role_key(user, room))
    if role is None:
        url = 'https://www.googleapis.com/calendar/v3/calendars/' + quote(room['calendar_id'], safe='') + '/events'
        params = {'maxResults': 1, 'singleEvents': 'true', 'fields': 'accessRole',
                  'timeMin': datetime.now(ZoneInfo(settings.TIME_ZONE)).isoformat()}
        token = access_token(user)
        try:
            response = requests.get(url, params=params, headers={'Authorization': f'Bearer {token}'}, timeout=10)
            if response.status_code == 401:
                token = access_token(user, force=True)
                response = requests.get(url, params=params, headers={'Authorization': f'Bearer {token}'}, timeout=10)
        except requests.RequestException:
            raise CalendarError('เชื่อมต่อ Google ไม่ได้ชั่วคราว / Google is temporarily unreachable.')
        if response.status_code == 401:
            raise reconnect()
        role = response.json().get('accessRole', 'none') if response.status_code == 200 else 'none'
        cache.set(_role_key(user, room), role, 600)
    return role in BOOKING_ROLES


def booking_url(user, room, day, start):
    """Google's own create-event screen, one hour long, writing on the room's own calendar.

    The department books rooms directly on the room calendar rather than inviting the room
    as a resource, so this selects that calendar with 'src'. Only the booking group reaches
    this view, and they hold the write access that 'src' requires.
    """
    begin = datetime.combine(day, start)
    params = {'action': 'TEMPLATE', 'ctz': settings.TIME_ZONE,
              'dates': f"{begin:%Y%m%dT%H%M%S}/{begin + timedelta(hours=1):%Y%m%dT%H%M%S}",
              'src': room['calendar_id']}
    name = room.get('name') or cache.get(f'room-name:{user.pk}:{_room_digest(room)}')
    if name:
        params['location'] = name
    return f'{CREATE_EVENT_URL}?{urlencode(params)}'
