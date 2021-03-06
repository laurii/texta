from django.conf.urls import url

from . import views


urlpatterns = [
    url(r'^$', views.index, name='index'),
    url(r'^save_permissions/?', views.save_permissions),
    url(r'^delete_user/?', views.delete_user),
    url(r'^add_dataset/?', views.add_dataset),
    url(r'^delete_dataset/?', views.delete_dataset),
]
