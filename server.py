from typing import List, Tuple, Optional
import httpx
import aiosqlite
import asyncio
import sqlite3
from mcp.server.fastmcp import FastMCP
from permit import Permit
from dotenv import load_dotenv
import os
import json

load_dotenv()  # load environment variables from .env

MAX_ALLOWED_DISH_PRICE = 10 # 10 dollar
DB_NAME = "food_ordering.db"
TENANT = 'default'

LOCAL_PDP_URL=  os.getenv("LOCAL_PDP_URL") 
PERMIT_API_KEY = os.getenv("PERMIT_API_KEY")
PROJECT_ID = os.getenv('PROJECT_ID') 
ENV_ID = os.getenv('ENV_ID') 
ACCESS_ELEMENTS_CONFIG_ID = os.getenv('ACCESS_ELEMENTS_CONFIG_ID')
OPERATION_ELEMENTS_CONFIG_ID = os.getenv('OPERATION_ELEMENTS_CONFIG_ID')

# Initialize FastMCP server
mcp = FastMCP("food_ordering")

# This will create dadjokes.db if it doesn't exist.
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

permit = Permit(
  pdp=LOCAL_PDP_URL,  
  token= PERMIT_API_KEY,
)

async def init_db():
  async with aiosqlite.connect(DB_NAME) as db:
    # Create tables for users, restaurants, and dishes.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            role TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            allowed_for_children BOOLEAN
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS dishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            name TEXT,
            price REAL,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    
    # Check if restaurants table is empty.
    cursor = await db.execute('SELECT COUNT(*) FROM restaurants')
    row = await cursor.fetchone()
    
    if row[0] == 0:
      # Populate the users table.
      users_data = [
        ("joe", "parent"),
        ("jane", "parent"),
        ("henry", "child"),
        ("rose", "child"),
      ]
      await db.executemany(
        "INSERT OR IGNORE INTO users (username, role) VALUES (?, ?)",
        users_data
      )

      # Populate the restaurants table.
      restaurants_data = [
        ("Pizza Palace", True),
        ("Burger Bonanza", True),
        ("Fancy French", False),
        ("Sushi World", False),
      ]
      await db.executemany(
        "INSERT OR IGNORE INTO restaurants (name, allowed_for_children) VALUES (?, ?)",
        restaurants_data
      )
      
      # Retrieve the restaurants with their IDs.
      cursor = await db.execute('SELECT id, name, allowed_for_children FROM restaurants')
      restaurants = await cursor.fetchall()
  
      # Populate the dishes table based on restaurant names.
      dishes_data = []
      for restaurant_id, restaurant_name, allowed in restaurants:
        if restaurant_name == "Pizza Palace":
          dishes_data.extend([
            (restaurant_id, "Cheese Pizza", 8.99),
            (restaurant_id, "Pepperoni Pizza", 10.99),
            (restaurant_id, "Veggie Pizza", 9.49),
          ])
        elif restaurant_name == "Burger Bonanza":
          dishes_data.extend([
            (restaurant_id, "Classic Burger", 7.99),
            (restaurant_id, "Deluxe Burger", 12.99),
            (restaurant_id, "Fries", 3.49),
          ])
        elif restaurant_name == "Fancy French":
          dishes_data.extend([
            (restaurant_id, "Escargot", 15.99),
            (restaurant_id, "Foie Gras", 19.99),
            (restaurant_id, "Truffle Pasta", 18.49),
          ])
        elif restaurant_name == "Sushi World":
          dishes_data.extend([
            (restaurant_id, "California Roll", 6.99),
            (restaurant_id, "Sushi Platter", 22.99),
            (restaurant_id, "Tempura", 9.99),
          ])

      await db.executemany(
        "INSERT OR IGNORE INTO dishes (restaurant_id, name, price) VALUES (?, ?, ?)",
        dishes_data
      )

      # Create Permit.io resource instances for each restaurant.
      await asyncio.gather(*[
        permit.api.resource_instances.create({
          "resource": "restaurants",
          "key": restaurant[0],
          "tenant": TENANT,
        })
        for restaurant in restaurants
      ])

      # Retrieve the users to synchronize with Permit.io.
      cursor = await db.execute("SELECT id, username, role FROM users")
      users = await cursor.fetchall()

      await asyncio.gather(*[
        permit.api.sync_user({
          "key": user[1],
        })
        for user in users
      ])

      # Separate restaurants allowed for children.
      children_restaurants = [r for r in restaurants if r[2]]
      
      # Assign roles using Permit.io based on user type.
      for _, username, role in users:
        if role == "parent":
          await permit.api.role_assignments.bulk_assign([
            {
              "user": username,
              "role": "parent",
              "tenant": TENANT,
              "resource_instance": f"restaurants:{r[0]}",
            }
            for r in restaurants
          ])
          await permit.api.role_assignments.bulk_assign([
            {
              "user": username,
              "role": "_Reviewer_",
              "tenant": TENANT,
              "resource_instance": f"restaurants:{r[0]}",
            }
            for r in restaurants
          ])
        elif role == "child":
          await permit.api.role_assignments.bulk_assign([
            {
              "user": username,
              "role": "child-can-order",
              "tenant": TENANT,
              "resource_instance": f"restaurants:{r[0]}",
            }
            for r in children_restaurants
          ]) 
    return await db.commit()
    
async def get_restaurant_by_name(restaurant_name: str) -> dict:
  """
  Fetches a restaurant by its name.
  """
  async with aiosqlite.connect(DB_NAME) as db:
      query = "SELECT id, name, allowed_for_children FROM restaurants WHERE name = ?"
      cursor = await db.execute(query, (restaurant_name,))
      row = await cursor.fetchone()
      await cursor.close()

  if row:
    return {
      "id": row[0],
      "name": row[1],
      "allowed_for_children": bool(row[2])
    }
  return None

@mcp.tool()
async def verify_access(username: str) -> Optional[str]:
  """
  To check if a user has access to the system after they provide their username.
  
  Args:
    username: The username to check.
  """
  async with aiosqlite.connect(DB_NAME) as db:
    query = "SELECT role FROM users WHERE username = ?"
    cursor = await db.execute(query, (username,))
    result = await cursor.fetchone()
    
    await cursor.close()

  if not result:
    return 
  return result[0]

@mcp.tool()
async def list_restaurants() -> List[Tuple[str, int]]:
  """
  Lists available restaurants. 
  If a restaurant is not for kids, the number 0 is after to the restaurant's name.
  """
  async with aiosqlite.connect(DB_NAME) as db:
      cursor = await db.execute("SELECT name, allowed_for_children FROM restaurants")
      rows = await cursor.fetchall()
      await cursor.close()
  if not rows:
      return "No restaurants available."
  return rows
        
@mcp.tool()
async def list_dishes(username: str, restaurant_name: str) ->  List[Tuple[str, int]]:
    """
    Lists the dishes available at a given restaurant along with their prices in dollars.
    Dishes are only listed if the user has the necessary permissions to access the restaurant.

    Args:
      username: The username of the user requesting dishes.
      restaurant_name: The name of the restaurant.
    """
    username = username.lower()
    restaurant = await get_restaurant_by_name(restaurant_name)

    if not restaurant:
      return "Restaurant not found"
    
    # Check if user is permitted in the restaurant
    permitted = await permit.check(username, 'read', f"restaurants:{restaurant['id']}")
    if not permitted:
      return f"Access denied. You are not permitted to view dishes from this restaurant."
      
    async with aiosqlite.connect(DB_NAME) as db:
        # Fetch dishes
        dishes_query = """
            SELECT name, price FROM dishes
            WHERE restaurant_id = ?
        """
        cursor = await db.execute(dishes_query, (restaurant['id'],))
        dishes = await cursor.fetchall()
        await cursor.close()

    if not dishes:
        return "No dishes available for this restaurant."
      
    return dishes
  
@mcp.tool()
async def order_dish(username: str, restaurant_name: str, dish_name: str) -> str:
  """
  Processes an order for a dish.

  Args:
    username: The username of the person ordering.
    restaurant_name: The name of the restaurant.
    dish_name: The name of the dish to order.
  """
  username = username.lower()
  restaurant = await get_restaurant_by_name(restaurant_name)
  if not restaurant:
      return "Restaurant not found."

  async with aiosqlite.connect(DB_NAME) as db:
      # Get dish price
      dish_cursor = await db.execute(
          "SELECT price FROM dishes WHERE name = ? AND restaurant_id = ?",
          (dish_name, restaurant["id"]),
      )
      dish = await dish_cursor.fetchone()
      await dish_cursor.close()

      if dish is None:
          return "Dish not found."

      # Get user role
      user_cursor = await db.execute(
          "SELECT role FROM users WHERE username = ?",
          (username,),
      )
      user = await user_cursor.fetchone()
      await user_cursor.close()

  # Check if user is permitted in the restaurant
  permitted = await permit.check(username, "operate", f"restaurants:{restaurant['id']}")

  # Apply price restriction for children
  if user[0] == "child" and dish[0] > MAX_ALLOWED_DISH_PRICE and not permitted:
      return (
          f"This dish costs ${dish[0]:.2f}, and you can only order dishes less than "
          f"${MAX_ALLOWED_DISH_PRICE:.2f}. To order this dish, you need to request an approval."
      )

  if permitted: 
    await permit.api.users.unassign_role({"user": username,  "role": "_Approved_", "resource_instance": f"restaurants:{restaurant['id']}", "tenant": TENANT})
    
  return f"Order successfully placed for {dish_name} from {restaurant_name}!"
  

@mcp.tool()
async def request_restaurant_access(username: str, restaurant_name: str) -> dict:
  """
  To request for parent's approval to be able to access a restaurant in order to view it's dishes.

  Args:
    username: The username of the person requesting access.
    restaurant_name: The name of the restaurant to request access for.
  """
 
  username = username.lower()  
  restaurant = await get_restaurant_by_name(restaurant_name)
  if not restaurant:
    return "Restaurant not found."
    
  url = f"https://api.permit.io/v2/facts/{PROJECT_ID}/{ENV_ID}/access_requests/{ACCESS_ELEMENTS_CONFIG_ID}/user/{username}/tenant/{TENANT}"
  payload = {
    "access_request_details": {
      "tenant": TENANT,
      "resource": "restaurants",
      "resource_instance": restaurant['id'],
      "role": 'child-can-order',
    },
    "reason": f"User {username} requests role {'child-can-order'} for {restaurant['name']} restaurant"
  }
  
  headers = {
      "authorization": f"Bearer {PERMIT_API_KEY}",
      "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
    response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 200 and response.status_code < 300:
      return "Your request has been successfully sent. Please check back later."
    else:
      return f"Request failed with status code {response.status_code}: {response.text} {restaurant['id']}"


@mcp.tool()
async def request_dish_approval(username: str, dish_name) -> dict:
  """
  To request a one-time operation approval to order a dish.

  Args:
    username: The username of the person requesting access.
    dish_name: The name of the dish to request approval for.
  """
  username = username.lower()
  login = await permit.elements.login_as(username, "default")

  async with aiosqlite.connect(DB_NAME) as db:
    query = """
        SELECT r.id 
        FROM restaurants r
        JOIN dishes d ON r.id = d.restaurant_id
        WHERE d.name = ?
    """
    cursor = await db.execute(query, (dish_name,))
    restaurant = await cursor.fetchone()
    await cursor.close()
  
  url = f"https://api.permit.io/v2/elements/{PROJECT_ID}/{ENV_ID}/config/{OPERATION_ELEMENTS_CONFIG_ID}/operation_approval"
  payload = {
      "access_request_details": {
          "tenant": TENANT,
          "resource": "restaurants",
          "resource_instance": restaurant[0],
      },
      "reason": f"User {username} requests approval to order {dish_name}"
  }
  headers = {
    "authorization": f"Bearer {login.element_bearer_token}",
    "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
    response = await client.post(url, json=payload, headers=headers)

    if response.status_code >= 200 and response.status_code < 300:
      return "Your request has been successfully sent. Please check back later."
    else:
      return f"Request failed with status code {response.status_code}: {response.text}"


@mcp.tool()
async def list_pending_restaurant_request(username: str, restaurant_name: str) -> dict:
  """
  Lists the pending requests to access a restaurant in order to be able to view it's dishes.

  Args:
    username: The username of the current user.
    restaurant_name: The name of the restaurant whose pending access requests are to be listed.
  """

  username = username.lower()  
  restaurant = await get_restaurant_by_name(restaurant_name)
  if not restaurant:
    return "Restaurant not found."
    
  url = f"https://api.permit.io/v2/facts/{PROJECT_ID}/{ENV_ID}/access_requests/{ACCESS_ELEMENTS_CONFIG_ID}/user/{username}/tenant/{TENANT}?resource_instance_id={restaurant['id']}"
  headers = {
      "authorization": f"Bearer {PERMIT_API_KEY}",
      "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
    response = await client.get(url, headers=headers)
    if response.status_code >= 200 and response.status_code < 300:
      string_data = response.content.decode('utf-8')
      data = json.loads(string_data)
      filtered_data = [
        {
            "access_request_id": item["id"],
            "reason": item["reason"]
        }
        for item in data['data']
        if item['status'] == 'pending'
      ]
      
      return filtered_data
    else:
      return f"Request failed with status code {response.status_code}: {response.text} {restaurant['id']}"


@mcp.tool()
async def list_pending_dish_request(username: str, restaurant_name: str) -> List[dict]:
  """
  Lists the pending one-time operation requests sent for permission to order a dish.

  Args:
    username: The username of the current user.
    restaurant_name: The name of the restaurant whose pending dish requests are to be listed.
  """

  username = username.lower()
  login = await permit.elements.login_as(username, TENANT)

  restaurant = await get_restaurant_by_name(restaurant_name)
  if not restaurant:
    return "Restaurant not found."
  
  url = f"https://api.permit.io/v2/elements/{PROJECT_ID}/{ENV_ID}/config/{OPERATION_ELEMENTS_CONFIG_ID}/operation_approval?element_id={OPERATION_ELEMENTS_CONFIG_ID}&resource=restaurants&status=pending&resource_instance={restaurant['id']}"
  headers = {
    "authorization": f"Bearer {login.element_bearer_token}",
    "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
    response = await client.get(url, headers=headers)
    if response.status_code >= 200 and response.status_code < 300:
      string_data = response.content.decode('utf-8')
      data = json.loads(string_data)
      filtered_data = [
        {
            "access_request_id": item["id"],
            "reason": item["reason"]
        }
        for item in data['data']
        if item['status'] == 'pending'
      ]
      
      return filtered_data
    else:
      return f"Request failed with status code {response.status_code}: {response.text}"


@mcp.tool()
async def approve_restaurant_access(username, access_request_id) -> dict:
  """
  Approve a pending restaurant access request.

  Args:
    username: The username of the current user.
    access_request_id: The ID of the access request which can be gotten from listing the pending access requests.
  """
  url = f"https://api.permit.io/v2/facts/{PROJECT_ID}/{ENV_ID}/access_requests/{ACCESS_ELEMENTS_CONFIG_ID}/user/{username}/tenant/{TENANT}/{access_request_id}/approve"
  payload = {
    "reviewer_comment": "grown up child",
    "role": "child-can-order"
  }
  headers = {
    "authorization": f"Bearer {PERMIT_API_KEY}",
    "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
    response = await client.put(url, json=payload, headers=headers)

    if response.status_code >= 200 and response.status_code < 300:
      return "Access has been granted"
    else:
      return f"Request failed with status code {response.status_code}: {response.text}"


@mcp.tool()
async def approve_opeartion_request(username, operation_approval_id) -> dict:
  """
  Approve a pending one-time operation request.

  Args:
    username: The username of the current user.
    operation_approval_id: The ID of the operation requet which can be gotten from listing the pending operation requests.
  """
  
  login = await permit.elements.login_as(username, TENANT)
  
  url = f"https://api.permit.io/v2/elements/{PROJECT_ID}/{ENV_ID}/config/{OPERATION_ELEMENTS_CONFIG_ID}/operation_approval/{operation_approval_id}/approve"
  payload = {
    "reviewer_comment": "transfer for a new client"
  }
  headers = {
    "authorization": f"Bearer {login.element_bearer_token}",
    "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
    response = await client.put(url, json=payload, headers=headers)
    if response.status_code >= 200 and response.status_code < 300:
      return "Access has been granted"
    else:
      return f"Request failed with status code {response.status_code}: {response.text} {login.element_bearer_token}"


if __name__ == "__main__":
  asyncio.run(init_db())
  mcp.run(transport="stdio")