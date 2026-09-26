import os, sys
sys.path.insert(0, r'D:\BIVIO\backend')
from dotenv import load_dotenv
load_dotenv(r'D:\BIVIO\backend\.env')

KEY    = os.getenv("AMAZON_ACCESS_KEY", "")
SECRET = os.getenv("AMAZON_SECRET_KEY", "")
TAG    = os.getenv("AMAZON_TAG", "")

print(f"KEY:    {KEY[:30]}...")
print(f"SECRET: {SECRET[:30]}...")
print(f"TAG:    {TAG}")

try:
    from amazon.paapi import AmazonApi
    api = AmazonApi(KEY, SECRET, TAG, "IT")
    result = api.search_items(keywords="arduino uno", item_count=2,
        resources=["ItemInfo.Title", "Offers.Listings.Price"])
    if result and result.search_result:
        for item in result.search_result.items:
            print(f"  {item.item_info.title.display_value}")
    else:
        print("Nessun risultato")
except Exception as e:
    print(f"ERRORE: {type(e).__name__}: {e}")
