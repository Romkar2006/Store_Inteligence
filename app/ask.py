from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection
import os
import httpx
import structlog
from typing import Dict, Any

from app.db import get_db_conn
from app.models import AskRequest, AskResponse
from app.metrics import get_store_metrics
from app.heatmap import get_store_heatmap
from app.funnel import get_store_funnel
from app.anomalies import get_store_anomalies

logger = structlog.get_logger()
router = APIRouter()

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

async def query_gemini_api(api_key: str, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }]
    }
    url = f"{GEMINI_API_URL}?key={api_key}"
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            logger.error("Gemini API call failed", status_code=response.status_code, body=response.text)
            raise HTTPException(status_code=502, detail=f"Gemini API returned error: {response.text}")
        
        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as e:
            logger.error("Failed to parse Gemini response", response=data, error=str(e))
            raise HTTPException(status_code=502, detail="Invalid response structure from Gemini API")

def run_rule_based_fallback(question: str, context: Dict[str, Any]) -> str:
    q = question.lower()
    metrics = context.get("metrics", {})
    heatmap = context.get("heatmap", {})
    funnel = context.get("funnel", {})
    anomalies = context.get("anomalies", {})
    
    # Extract details
    visitors = metrics.get("unique_visitors", 0)
    conversion = metrics.get("conversion_rate", 0.0) * 100
    queue = metrics.get("current_queue_depth", 0)
    abandon = metrics.get("abandonment_rate", 0.0) * 100
    
    zones = heatmap.get("zones", [])
    
    # 1. Dwell time / Hot zone questions
    if any(keyword in q for keyword in ["dwell", "hot", "cold", "shelf", "mirror", "gondola", "table", "aisle"]):
        if not zones:
            return "Based on store data, there are no recorded zone dwell activities today."
        highest_heat = zones[0]
        highest_visits = max(zones, key=lambda z: z["visit_count"]) if zones else None
        
        answer = f"The busiest zone in terms of dwell time is the {highest_heat['zone_id']} with a heat score of {highest_heat['heat_score']}/100 and an average dwell time of {highest_heat['avg_dwell_ms'] // 1000}s."
        if highest_visits and highest_visits["zone_id"] != highest_heat["zone_id"]:
            answer += f" However, the {highest_visits['zone_id']} recorded the most raw visitor count with {highest_visits['visit_count']} visits."
        return answer
        
    # 2. Abandonment / Queue questions
    elif any(keyword in q for keyword in ["abandon", "queue", "checkout", "billing", "line"]):
        return f"Today, the current billing queue depth is {queue} people. The queue abandonment rate is {abandon:.1f}%, representing the percentage of customers who joined the billing queue but left without a purchase."
        
    # 3. Visitors / Traffic questions
    elif any(keyword in q for keyword in ["visitor", "customer", "people", "traffic", "shoppers"]):
        return f"The store has welcomed {visitors} unique shoppers today (excluding staff members). The total session count recorded across all entrances is {funnel.get('session_count', 0)} sessions."
        
    # 4. Conversion / Purchase questions
    elif any(keyword in q for keyword in ["conversion", "purchase", "sales", "buyer", "pay"]):
        return f"The store conversion rate today is currently at {conversion:.1f}%. This is computed using a 2-minute camera entry-to-exit correlation with POS transaction times."
        
    # 5. Default general summary fallback
    else:
        anom_txt = f"There are {len(anomalies.get('anomalies', []))} operational anomalies currently flagged."
        if anomalies.get('anomalies'):
            anom_txt += f" Latest alert: {anomalies['anomalies'][0]['detail']}."
            
        return f"Here is a summary of the store metrics today: {visitors} unique shoppers visited the store with a conversion rate of {conversion:.1f}% and a checkout queue abandonment rate of {abandon:.1f}%. The checkout queue depth is currently {queue}. {anom_txt}"

@router.post("/stores/{store_id}/ask", response_model=AskResponse)
async def ask_store_question(
    store_id: str,
    payload: AskRequest,
    conn: AsyncConnection = Depends(get_db_conn)
):
    question = payload.question
    
    # 1. Fetch store context dynamically from DB using current routers
    try:
        metrics_data = await get_store_metrics(store_id, conn)
        heatmap_data = await get_store_heatmap(store_id, conn)
        funnel_data = await get_store_funnel(store_id, conn)
        anomalies_data = await get_store_anomalies(store_id, conn)
    except Exception as e:
        logger.error("Failed to gather store context", store_id=store_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to gather store context: {str(e)}")

    context = {
        "metrics": metrics_data.model_dump(),
        "heatmap": heatmap_data.model_dump(),
        "funnel": funnel_data.model_dump(),
        "anomalies": anomalies_data.model_dump()
    }
    
    # 2. Check for Gemini API key
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        prompt = f"""You are an expert AI retail analytics assistant for the "Purplle" beauty retail chain.
Your goal is to answer the user's natural language question about store operations based strictly on the provided real event data context.

Context Data for Store "{store_id}":
- KPI Metrics: {context['metrics']}
- Zone Dwells & Heatmap: {context['heatmap']}
- Shopper Conversion Funnel: {context['funnel']}
- Active System Anomalies: {context['anomalies']}

Guidelines:
1. Provide a professional, concise, and direct answer (1-3 sentences max).
2. Do not speculate or make up metrics. If the context does not contain enough information to answer, state what you know and note the lack of specific details.
3. Keep the tone operations-focused (e.g. "We currently see...", "Dwell times indicate...").

User Question: "{question}"
AI Response:"""
        
        try:
            answer = await query_gemini_api(api_key, prompt)
            mode = "genai"
        except Exception as e:
            logger.warn("Gemini API query failed, falling back to rule-based engine", error=str(e), exc_info=True)
            answer = run_rule_based_fallback(question, context)
            mode = "fallback"
    else:
        # Run rule-based fallback
        answer = run_rule_based_fallback(question, context)
        mode = "fallback"
        # Append fallback warning to response answer
        answer = f"[Fallback Mode] {answer}"
        
    return AskResponse(
        store_id=store_id,
        question=question,
        answer=answer,
        mode=mode
    )
