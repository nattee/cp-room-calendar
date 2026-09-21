import logging
import time
from datetime import date, time as day_time

from authlib.integrations.base_client.errors import OAuthError
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST
from requests import RequestException

from .google import (EVENT_SCOPE, CalendarError, booking_url, list_day, list_events, may_book,
                     oauth, room_names)
from .models import GoogleToken

logger = logging.getLogger(__name__)


@require_GET
def index(request):
    return render(request, 'index.html', {
        'rooms': [{'key': r['key'], 'name': r.get('name')} for r in settings.ROOMS],
        'domain': settings.ORG_DOMAIN,
    })


@require_GET
def health(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return JsonResponse({'status': 'ok'})


@require_POST
def signin(request):
    try:
        return oauth.google.authorize_redirect(
            request, settings.PUBLIC_URL + '/auth/google/callback',
            access_type='offline', prompt='consent', hd=settings.ORG_DOMAIN,
        )
    except (OAuthError, RequestException):
        return render(request, 'error.html', {'message': 'Google is temporarily unreachable. Please try again.'}, status=503)


@require_GET
def callback(request):
    try:
        # Authlib validates state, nonce, issuer, audience, expiry, and signature.
        token = oauth.google.authorize_access_token(request)
        claims = token.get('userinfo', {})
        email = claims.get('email', '').lower()
        if (claims.get('email_verified') is not True
                or claims.get('hd') not in settings.ALLOWED_HOSTED_DOMAINS
                or email.rsplit('@', 1)[-1] != settings.ORG_DOMAIN
                or not claims.get('sub')):
            return render(request, 'error.html', {'message': f'Please use your @{settings.ORG_DOMAIN} organizational account.'}, status=403)
        scopes = set(token.get('scope', '').split())
        if EVENT_SCOPE not in scopes:
            return render(request, 'error.html', {'message': 'Please allow read-only calendar access when signing in.'}, status=403)
        user, created = User.objects.get_or_create(username='google:' + claims['sub'])
        if created:
            user.set_unusable_password()
        if not user.is_active:
            return render(request, 'error.html', {'message': 'This account is disabled.'}, status=403)
        user.email = email
        user.save()
        record, _ = GoogleToken.objects.get_or_create(user=user)
        if not token.get('refresh_token') and record.encrypted:
            token['refresh_token'] = record.read().get('refresh_token')
        if not token.get('refresh_token'):
            return render(request, 'error.html', {'message': 'Google did not grant persistent access. Please try signing in again and allow access.'}, status=403)
        token.setdefault('expires_at', time.time() + int(token.get('expires_in', 3600)))
        record.write(token)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        return redirect('index')
    except Exception as exc:
        # OAuth exceptions can contain authorization codes/tokens: log only the type.
        logger.warning('Google callback failed (%s)', type(exc).__name__)
        return render(request, 'error.html', {'message': 'Sign-in could not be completed. Please start again from the sign-in page.'}, status=400)


@require_POST
def signout(request):
    logout(request)
    return redirect('index')


@require_GET
def rooms(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'signin', 'message': 'Please sign in.'}, status=401)
    try:
        return JsonResponse({'rooms': room_names(request.user)})
    except CalendarError as exc:
        return JsonResponse({'error': exc.code, 'message': exc.message}, status=exc.status)


@require_GET
def events(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'signin', 'message': 'Please sign in to view room calendars.'}, status=401)
    room = next((r for r in settings.ROOMS if r['key'] == request.GET.get('room')), None)
    if room is None:
        return JsonResponse({'error': 'room', 'message': 'Unknown room.'}, status=400)
    view = request.GET.get('view', 'day')
    if view not in ('day', 'month'):
        return JsonResponse({'error': 'view', 'message': 'Choose day or month view.'}, status=400)
    try:
        selected = date.fromisoformat(request.GET.get('date', ''))
        if selected.year < 2000 or selected.year > 2100:
            raise ValueError
    except ValueError:
        return JsonResponse({'error': 'date', 'message': 'Choose a date between 2000 and 2100.'}, status=400)
    try:
        return JsonResponse(list_events(request.user, room, selected, view=view))
    except CalendarError as exc:
        response = JsonResponse({'error': exc.code, 'message': exc.message}, status=exc.status)
        if exc.status == 429:
            response['Retry-After'] = '120'
        return response


@require_GET
def day(request):
    """One day across every room, for the operations team preparing the rooms."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'signin', 'message': 'Please sign in to view room calendars.'}, status=401)
    try:
        selected = date.fromisoformat(request.GET.get('date', ''))
        if selected.year < 2000 or selected.year > 2100:
            raise ValueError
    except ValueError:
        return JsonResponse({'error': 'date', 'message': 'Choose a date between 2000 and 2100.'}, status=400)
    try:
        return JsonResponse(list_day(request.user, selected))
    except CalendarError as exc:
        response = JsonResponse({'error': exc.code, 'message': exc.message}, status=exc.status)
        if exc.status == 429:
            response['Retry-After'] = '600'
        return response


@require_GET
def book(request):
    """Send a signed-in person to Google's create-event screen with this room invited."""
    if not request.user.is_authenticated:
        return redirect('index')
    room = next((r for r in settings.ROOMS if r['key'] == request.GET.get('room')), None)
    if room is None:
        return render(request, 'error.html', {'message': 'ไม่พบห้องนี้ / Unknown room.'}, status=400)
    try:
        selected = date.fromisoformat(request.GET.get('date', ''))
        start = day_time.fromisoformat(request.GET.get('start', ''))
        if selected.year < 2000 or selected.year > 2100 or start.second or start.microsecond:
            raise ValueError
    except ValueError:
        return render(request, 'error.html', {'message': 'วันที่หรือเวลาไม่ถูกต้อง / Choose a valid date and time.'}, status=400)
    try:
        permitted = may_book(request.user, room)
    except CalendarError as exc:
        return render(request, 'error.html', {'message': exc.message}, status=exc.status)
    if not permitted:
        return render(request, 'error.html', {
            'message': 'คุณไม่มีสิทธิ์จองห้องนี้ / You do not have booking rights for this room.'}, status=403)
    return redirect(booking_url(request.user, room, selected, start))
