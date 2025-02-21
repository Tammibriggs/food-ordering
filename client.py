import asyncio
from typing import Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from anthropic import Anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt


load_dotenv()  # load environment variables from .env
console = Console()

class MCPClient:
    
  def __init__(self):
    # Initialize session and client objects
    self.session: Optional[ClientSession] = None
    self.exit_stack = AsyncExitStack()
    self.anthropic = Anthropic()
    self.username = None
    
  
  async def connect_to_server(self, server_script_path: str):
    """Connect to an MCP server

    Args:
      server_script_path: Path to the server script (.py or .js)
    """
    is_python = server_script_path.endswith('.py')
    is_js = server_script_path.endswith('.js')
    if not (is_python or is_js):
      raise ValueError("Server script must be a .py or .js file")

    command = "python" if is_python else "node"
    server_params = StdioServerParameters(
      command=command,
      args=[server_script_path],
      env=None
    )

    stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
    self.stdio, self.write = stdio_transport
    self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

    await self.session.initialize()
    
    # List available tools
    response = await self.session.list_tools()
    tools = response.tools
    console.print("\nConnected to server with tools:", [tool.name for tool in tools])
    
        
  async def process_query(self, query: str) -> str:
    """Process a query using Claude and available tools"""
    messages = [
      {
        "role": "user",
        "content": query
      }
    ]

    response = await self.session.list_tools()
    available_tools = [{
      "name": tool.name,
      "description": tool.description,
      "input_schema": tool.inputSchema
    } for tool in response.tools]
    
    # Initial Claude API call
    response = self.anthropic.messages.create(
      model="claude-3-5-sonnet-20241022",
      max_tokens=1000,
      messages=messages,
      tools=available_tools
    )

    # Process response and handle tool calls
    tool_results = []
    final_text = []
    assistant_message_content = []
    for content in response.content:
        if content.type == 'text':
            final_text.append(content.text)
            assistant_message_content.append(content)
        elif content.type == 'tool_use':
            tool_name = content.name
            tool_args = content.input

            # Execute tool call
            result = await self.session.call_tool(tool_name, tool_args)
            tool_results.append({"call": tool_name, "result": result})
            final_text.append(f"[Calling tool {tool_name} with args {tool_args}]")

            assistant_message_content.append(content)
            messages.append({
                "role": "assistant",
                "content": assistant_message_content
            })
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": content.id,
                        "content": result.content
                    }
                ]
            })

            # Get next response from Claude
            response = self.anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=messages,
                tools=available_tools
            )

            final_text.append(response.content[0].text)

    return "\n".join(final_text)
  
  async def chat_loop(self):
    """Run an interactive chat loop"""
    console.print("[bold green]Welcome to the Family Food Ordering System[/bold green]")
    console.print("Type your queries or 'quit' to exit.")
    
    while True:
      try:
        if not self.username:
          username = console.input("First, what is [i]your[/i] [bold blue]name[/]? :smiley: ").strip().lower()
          
          if username == 'quit':
            break
          
          if username: 
            response = await self.session.call_tool('verify_access', {"username": username})

            if not response.content:
              console.print("Access denied")
              break 
            
            console.print(response.content)
        
        else:
          query = console.input("\nQuery: ").strip()

          if query.lower() == 'quit':
            break

          response = await self.process_query(query)
          print("\n" + response)
          
      except Exception as e:
        print(f"\nError: {str(e)}")
  
  # async def terminal_ui():
  #   console.print("[bold green]Welcome to the Family Food Ordering System[/bold green]")
  #   while True:
  #       console.print("\n[bold blue]Options:[/bold blue]")
  #       console.print("1. List Restaurants")
  #       console.print("2. List Dishes for a Restaurant")
  #       console.print("3. Request Restaurant Access")
  #       console.print("4. Order a Dish")
  #       console.print("5. Exit")
  #       choice = Prompt.ask("Enter your choice", choices=["1", "2", "3", "4", "5"])
        
  #       if choice == "1":
  #           result = await list_restaurants()  # ✅ Use await instead of asyncio.run
  #           console.print(result)
  #       elif choice == "2":
  #           restaurant = Prompt.ask("Enter restaurant name")
  #           result = await list_dishes(restaurant)
  #           console.print(result)
  #       elif choice == "3":
  #           username = Prompt.ask("Enter your username")
  #           restaurant = Prompt.ask("Enter restaurant to request access for")
  #           result = await request_restaurant_access(username, restaurant)
  #           console.print(result)
  #       elif choice == "4":
  #           username = Prompt.ask("Enter your username")
  #           restaurant = Prompt.ask("Enter restaurant name")
  #           dish = Prompt.ask("Enter dish name")
  #           result = await order_dish(username, restaurant, dish)
  #           console.print(result)
  #       elif choice == "5":
  #           console.print("Goodbye!")
  #           break


  async def cleanup(self):
    """Clean up resources"""
    await self.exit_stack.aclose()
  
async def main():
  if len(sys.argv) < 2:
    print("Usage: python client.py <path_to_server_script>")
    sys.exit(1)

  client = MCPClient()
  try:
      await client.connect_to_server(sys.argv[1])
      await client.chat_loop()
  finally:
      await client.cleanup()

if __name__ == "__main__":
    import sys
    asyncio.run(main())
