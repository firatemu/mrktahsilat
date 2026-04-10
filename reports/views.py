import json
import os
import requests

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

@login_required
def reports_chat_page(request):
    return render(request, "reports/chat.html", {})

@require_POST
@csrf_exempt
@login_required
def n8n_chat_query(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    query = (payload.get("query") or "").strip()
    if not query:
        return HttpResponseBadRequest("query is required")

    webhook_url = os.environ.get("N8N_WEBHOOK_URL", "").strip()
    integration_key = os.environ.get("MRK_INTEGRATION_KEY", "").strip()

    if not webhook_url or not integration_key:
        return JsonResponse({"error": "Server is not configured"}, status=500)

    try:
        r = requests.post(
            webhook_url,
            json={
                "query": query,
                "user": {
                    "id": request.user.id,
                    "username": request.user.get_username(),
                }
            },
            headers={
                "X-MRK-INTEGRATION-KEY": integration_key,
                "Content-Type": "application/json",
            },
            timeout=60,
        )
    except requests.RequestException as e:
        return JsonResponse({"error": "n8n request failed", "details": str(e)}, status=502)

    try:
        data = r.json()
    except Exception:
        return JsonResponse({"error": "Invalid response from n8n", "status": r.status_code, "text": r.text[:500]}, status=502)

    return JsonResponse({"status": r.status_code, "data": data}, status=200)
