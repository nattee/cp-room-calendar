from django.urls import path
from viewer import views

urlpatterns = [
    path('', views.index, name='index'),
    path('healthz', views.health, name='health'),
    path('auth/google/start', views.signin, name='signin'),
    path('auth/google/callback', views.callback, name='callback'),
    path('auth/logout', views.signout, name='signout'),
    path('api/events', views.events, name='events'),
    path('api/rooms', views.rooms, name='rooms'),
    path('api/day', views.day, name='day'),
    path('book', views.book, name='book'),
]
