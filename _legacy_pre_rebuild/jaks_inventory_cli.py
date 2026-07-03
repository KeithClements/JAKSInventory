import sys
import argparse

# Import your main app and backend logic
from jaks_inventory.main import main as launch_gui
from db import get_all_quotes, create_quote, get_all_products

def cli():
    parser = argparse.ArgumentParser(description="JAKS Inventory CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Launch GUI
    subparsers.add_parser("launch-gui", help="Launch the main GUI application")

    # Quote commands
    quote_parser = subparsers.add_parser("quote", help="Quote operations")
    quote_sub = quote_parser.add_subparsers(dest="quote_cmd")
    quote_sub.add_parser("list", help="List all quotes")
    create_parser = quote_sub.add_parser("create", help="Create a new quote")
    create_parser.add_argument("--customer-id", type=int, required=True, help="Customer ID")
    # Add more quote subcommands as needed

    # Inventory commands
    inv_parser = subparsers.add_parser("inventory", help="Inventory operations")
    inv_sub = inv_parser.add_subparsers(dest="inv_cmd")
    inv_sub.add_parser("list", help="List all inventory items")

    args = parser.parse_args()

    if args.command == "launch-gui":
        launch_gui()
    elif args.command == "quote":
        if args.quote_cmd == "list":
            for q in get_all_quotes():
                print(q)
        elif args.quote_cmd == "create":
            quote_id = create_quote(args.customer_id)
            print(f"Created quote with ID: {quote_id}")
        else:
            parser.print_help()
    elif args.command == "inventory":
        if args.inv_cmd == "list":
            for p in get_all_products():
                print(p)
        else:
            parser.print_help()
    else:
        parser.print_help()

if __name__ == "__main__":
    cli()
