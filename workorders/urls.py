# workorders/urls.py
from django.urls import path

from . import views

app_name = "workorders"

urlpatterns = [
    path("", views.workorder_list, name="list"),
    path("<int:pk>/", views.workorder_detail, name="detail"),

    path("<int:pk>/reserve/", views.workorder_reserve, name="reserve"),
    path("<int:pk>/release/", views.workorder_release_reservation, name="release_reservation"),

    path("<int:pk>/issue/", views.workorder_issue, name="issue"),
    path("<int:pk>/return/", views.workorder_return, name="return_"),

    path("<int:pk>/reserve/new/", views.workorder_reserve_page, name="reserve_page"),
    path("<int:pk>/issue/new/", views.workorder_issue_page, name="issue_page"),

    path("new/", views.workorder_create, name="create"),
    path("<int:pk>/lines/new/", views.workorder_line_create, name="line_create"),

    path("<int:pk>/approve/", views.workorder_approve, name="approve"),
    path("<int:pk>/complete/", views.workorder_complete, name="complete"),

    path("<int:pk>/pause/", views.workorder_pause, name="pause"),
    path("<int:pk>/resume/", views.workorder_resume, name="resume"),

    path("<int:pk>/cancel/", views.workorder_cancel, name="cancel"),
    

]
