from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("items/", views.item_list, name="item_list"),
    path("items/new/", views.item_create, name="item_create"),
    path("items/<int:item_id>/", views.item_detail, name="item_detail"),
    path("items/<int:item_id>/edit/", views.item_edit, name="item_edit"),

    path("movements/new/", views.movement_create, name="movement_create"),

    path("ajax/locations/", views.locations_by_warehouse, name="locations_by_warehouse"),
    path("transfer/new/", views.transfer_create, name="transfer_create"),
    path("adjustments/new/", views.adjustment_create, name="adjustment_create"),
    path("exports/movements.csv", views.export_movements_csv, name="export_movements_csv"),
    path("exports/snapshots.csv", views.export_snapshots_csv, name="export_snapshots_csv"),
    path("movements/<int:movement_id>/void/", views.movement_void, name="movement_void"),
    path("ajax/stock/", views.stock_by_item_location, name="stock_by_item_location"),
    path("cycle-count/", views.cycle_count, name="cycle_count"),
    path("reservations/new/", views.reservation_manage, name="reservation_manage"),
    path("reserves/new/", views.reserve_create, name="reserve_create"),
path("releases/new/", views.release_create, name="release_create"),
path("ajax/stock/", views.stock_by_item_location, name="stock_by_item_location"),

    


]
