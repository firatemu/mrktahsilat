from django.urls import path
from . import views

urlpatterns = [
    path("reports/chat/", views.reports_chat_page, name="reports_chat_page"),
    path("api/n8n/chat-query/", views.n8n_chat_query, name="n8n_chat_query"),
]
