"""Order Service - Lightweight JSON file persistence for order history."""

import json
import os
from datetime import datetime
from typing import Optional
from uuid import uuid4

# Data directory and file path
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)


def _read_orders() -> list[dict]:
    """Read all orders from JSON file."""
    if not os.path.exists(ORDERS_FILE):
        return []
    try:
        with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _write_orders(orders: list[dict]) -> None:
    """Write orders to JSON file."""
    with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def save_order(
    image_url: str,
    template_id: str,
    template_title: str,
    user_id: str = "anonymous_user",
    clothing_prompt: Optional[str] = None,
    custom_scene: Optional[str] = None,
) -> dict:
    """
    Save a new order to persistent storage.
    
    Args:
        image_url: URL of the generated image
        template_id: Template ID used
        template_title: Template title for display
        user_id: User ID
        clothing_prompt: The clothing prompt used
        custom_scene: Custom scene if user provided one
        
    Returns:
        The created order record
    """
    orders = _read_orders()
    
    order = {
        "id": str(uuid4()),
        "user_id": user_id,
        "image_url": image_url,
        "template_id": template_id,
        "template_title": template_title,
        "clothing_prompt": clothing_prompt,
        "custom_scene": custom_scene,
        "created_at": datetime.now().isoformat(),
        "status": "completed",
    }
    
    orders.append(order)
    _write_orders(orders)
    
    return order


def get_user_orders(user_id: str = "anonymous_user") -> list[dict]:
    """
    Get all orders for a user, sorted by newest first.
    
    Args:
        user_id: User ID to filter by
        
    Returns:
        List of order records
    """
    orders = _read_orders()
    user_orders = [o for o in orders if o.get("user_id") == user_id]
    # Sort by created_at descending (newest first)
    user_orders.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return user_orders


def get_order_by_id(order_id: str) -> Optional[dict]:
    """Get a specific order by ID."""
    orders = _read_orders()
    for order in orders:
        if order.get("id") == order_id:
            return order
    return None


def delete_order(order_id: str) -> bool:
    """Delete an order by ID."""
    orders = _read_orders()
    original_len = len(orders)
    orders = [o for o in orders if o.get("id") != order_id]
    if len(orders) < original_len:
        _write_orders(orders)
        return True
    return False
