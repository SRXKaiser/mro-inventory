from django.urls import path
from . import views

urlpatterns = [
    # Warehouses
    path("warehouses/", views.warehouse_list, name="warehouse_list"),
    path("warehouses/new/", views.warehouse_create, name="warehouse_create"),
    path("warehouses/<int:warehouse_id>/edit/", views.warehouse_edit, name="warehouse_edit"),

    # Locations
    path("", views.location_list, name="location_list"),
    path("new/", views.location_create, name="location_create"),
    path("<int:location_id>/edit/", views.location_edit, name="location_edit"),
]
