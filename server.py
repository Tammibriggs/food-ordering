from typing import Any
import httpx
import aiosqlite
import asyncio
import sqlite3
from mcp.server.fastmcp import FastMCP
from permit import Permit
from dotenv import load_dotenv
import os
import re
import unicodedata

load_dotenv()  # load environment variables from .env

MAX_ALLOWED_DISH_PRICE = 10
DB_NAME = "food_ordering.db"
PROJECT_ID = os.getenv("PROJECT_ID")
ENV_ID = os.getenv("PERMIT_API_KEY")
ELEMENTS_CONFIG_ID = os.getenv("ELEMENTS_CONFIG_ID")

# Initialize FastMCP server
mcp = FastMCP("food_ordering")

# This will create dadjokes.db if it doesn't exist.
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

permit = Permit(
    pdp="https://cloudpdp.api.permit.io",  
    token= ENV_ID,
)

def slugify(text):
  """
  Converts a string to a URL-friendly slug.
  """
  text = unicodedata.normalize('NFKD', text) 
  text = text.encode('ascii', 'ignore').decode('utf-8')  
  text = re.sub(r'[^\w\s-]', '', text).lower() 
  text = re.sub(r'\s+', '-', text).strip() 
  return text

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
        ("jacob", "parent"),
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
          "tenant": "default",
        })
        for restaurant in restaurants
      ])

      
      # Retrieve the users to synchronize with Permit.io.
      cursor = await db.execute("SELECT id, username, role FROM users")
      users = await cursor.fetchall()

      await asyncio.gather(*[
        permit.api.sync_user({
          "key": slugify(user[1]),
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
              "user": slugify(username),
              "role": "parent",
              "tenant": "default",
              "resource_instance": f"restaurants:{r[0]}",
            }
            for r in restaurants
          ])
        elif role == "child":
          await permit.api.role_assignments.bulk_assign([
            {
              "user": slugify(username),
              "role": "child-can-order",
              "tenant": "default",
              "resource_instance": f"restaurants:{r[0]}",
            }
            for r in children_restaurants
          ]) 
    await db.commit()
    return 'working'
    
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
async def verify_access(username: str) -> bool:
  """
  To check if a user has access to the system after they provide their username.
  
  Args:
    username: The username to check.
  """
  async with aiosqlite.connect(DB_NAME) as db:
    query = "SELECT COUNT(*) FROM users WHERE username = ?"
    cursor = await db.execute(query, (username,))
    result = await cursor.fetchone()
    await cursor.close()

  if result and result[0] > 0:
    return True
  return False

@mcp.tool()
async def list_restaurants() -> str:
  """
  Lists available restaurants. 
  If a restaurant is not for kids, the text "not for kids" is appended
  to the restaurant's name.
  """
  async with aiosqlite.connect(DB_NAME) as db:
      cursor = await db.execute("SELECT id, name, allowed_for_children FROM restaurants")
      rows = await cursor.fetchall()
      await cursor.close()
  if not rows:
      return "No restaurants available."
  return rows
    
  # return "\n".join([f"- {row[2]}{' not for kids' if row[1] else ''}" for row in rows])
        
@mcp.tool()
async def list_dishes(username: str, restaurant_name: str) -> str:
    """
    Lists dishes for a given restaurant along with their price.
    It also checks if the user is permitted to access the restaurant.

    Args:
        username: The username of the user requesting dishes.
        restaurant_name: The name of the restaurant.
    """
    
    restaurant = await get_restaurant_by_name(restaurant_name)
    if not restaurant:
      return "Restaurant not found"
    
    # Check if user is permitted in the restaurant
    permitted = permit.check(slugify(username), 'read', f"restaurants:{restaurant['id']}")
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

    return "\n".join([f"- {name} (${price:.2f})" for name, price in dishes])
  
@mcp.tool()
async def order_dish(username: str, restaurant_name: str, dish_name: str) -> str:
  """
  Processes an order for a dish. If the dish is above a specific price, a one-time approval request is required.

  Args:
      username: The username of the person ordering.
      restaurant_name: The name of the restaurant.
      dish_name: The name of the dish to order.

  Returns:
      str: Order confirmation or approval request message.
  """
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
  permitted = await permit.check(slugify(username), "operate", f"restaurants:{restaurant['id']}")

  # Apply price restriction for children
  if user[0] == "child" and dish[0] > MAX_ALLOWED_DISH_PRICE and not permitted:
      return (
          f"This dish costs ${dish[0]:.2f}, and you can only order dishes less than "
          f"${MAX_ALLOWED_DISH_PRICE:.2f}. To order this dish, you need to request approval."
      )

  return f"Order successfully placed for {dish_name} from {restaurant_name}!"
  

@mcp.tool()
async def request_restaurant_access(username: str, restaurant_name: str) -> dict:
  """
  To requests permanent access to a restaurant.

  Args:
    username: The username of the person requesting access.
    restaurant_name: The name of the restaurant to request access for.

  """
  
  login = await permit.elements.login_as({ "userId": slugify(username), "tenant": "default"})
  print(login)
  
  restaurant = await get_restaurant_by_name(restaurant_name)
  if not restaurant:
      return "Restaurant not found."
    
  url = f"https://api.permit.io/v2/facts/{PROJECT_ID}/{ENV_ID}/access_requests/{ELEMENTS_CONFIG_ID}/user/{slugify(username)}/tenant/default"
  payload = {
      "access_request_details": {
          "tenant": "default",
          "resource": "restaurants",
          "resource_instance": restaurant['id'],
          "role": 'child-can-order',
      },
      "reason": f"User {username} requests role {'child-can-order'} for {restaurant['name']} restaurant"
  }
  headers = {
      "authorization": "Bearer YOUR_API_SECRET_KEY",
      "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
      await client.post(url, json=payload, headers=headers)
      return "Your request has been sent. Please check back later."

@mcp.tool()
async def request_dish_approval(username: str, dish_name) -> dict:
  """
  To request a one-time approval to order a dish.

  Args:
    username: The username of the person requesting access.
    dish_name: The name of the dish to request approval for.
  """
  
  login = await permit.elements.login_as({ "userId": slugify(username), "tenant": "default"})
  print(login)
  
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
  
  url = "https://api.permit.io/v2/elements/{PROJECT_ID}/{ENV_ID}/config/{ELEMENTS_CONFIG_ID}/operation_approval"
  payload = {
      "access_request_details": {
          "tenant": "default",
          "resource": "restaurants",
          "resource_instance": restaurant[0],
      },
      "reason": f"User {username} requests approval to order {dish_name}"
  }
  headers = {
      "authorization": "Bearer YOUR_API_SECRET_KEY",
      "Content-Type": "application/json",
  }
  async with httpx.AsyncClient() as client:
      await client.post(url, json=payload, headers=headers)
      return 'You request has been successfully sent. Please check back later.'


if __name__ == "__main__":
  asyncio.run(init_db())
  mcp.run(transport="stdio")

  