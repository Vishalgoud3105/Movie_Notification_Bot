import requests
from bs4 import BeautifulSoup
import time
import datetime
import os

# --- Configuration ---
# Your Telegram bot and chat information
# Values will be pulled from Heroku's environment variables
TELEGRAM_API_TOKEN = os.environ.get("TELEGRAM_API_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# BookMyShow details for the movie
# Values will be pulled from Heroku's environment variables
MOVIE_CODE = os.environ.get("MOVIE_CODE")
CITY_CODE = os.environ.get("CITY_CODE")
MOVIE_SLUG = "demon-slayer-kimetsu-no-yaiba-infinity-castle"

# --- Functions ---
def send_telegram_message(message):
    """Sends a message to the Telegram bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message
    }
    try:
        requests.post(url, json=payload)
        print("Telegram notification sent.")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def get_monitor_urls():
    """Generates the list of URLs to monitor for each day."""
    monitor_urls = {}
    
    # Define the date range (Year, Month, Day)
    start_date = datetime.date(2025, 9, 12)
    end_date = datetime.date(2025, 9, 18)
    
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y%m%d")
        
        # Construct the URL based on the consistent format
        monitor_urls[f"Showtimes on {current_date.strftime('%b %d')}"] = (
            f"https://in.bookmyshow.com/movies/{CITY_CODE}/{MOVIE_SLUG}/buytickets/{MOVIE_CODE}/{date_str}"
        )
        
        current_date += datetime.timedelta(days=1)
        
    return monitor_urls

def check_for_shows():
    """Checks the BMS pages for available shows at specific cinemas."""
    print("Checking for new showtimes...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    found_shows = []
    
    monitor_urls = get_monitor_urls()

    cinemas_to_monitor = ["PVR Irrum Manzil", "PVR Nexus Mall"] 

    for label, url in monitor_urls.items():
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
            soup = BeautifulSoup(response.content, 'html.parser')

            # Find cinema blocks and check for showtimes
            theater_blocks = soup.find_all('li', class_='list-item')
            
            for block in theater_blocks:
                cinema_name_element = block.find('a', class_='__name')
                if cinema_name_element:
                    cinema_name = cinema_name_element.text.strip()
                    if cinema_name in cinemas_to_monitor:
                        # Within the cinema block, check for Japanese 4DX
                        format_divs = block.find_all('span', class_='show-format-name')
                        for format_div in format_divs:
                            if "Japanese" in format_div.text and "4DX" in format_div.text:
                                # Find showtimes for this specific format
                                showtime_div = format_div.find_parent('li').find('ul', class_='show-list')
                                if showtime_div:
                                    # Check for available (not sold out) shows
                                    available_shows = showtime_div.find_all('li', class_='show-item')
                                    if available_shows:
                                        showtime_details = [st.find('a', class_='__time').text.strip() for st in available_shows if st.find('a', class_='__time')]
                                        found_shows.append(f"New Japanese 4DX show added at {cinema_name} on {label}! Showtimes: {', '.join(showtime_details)}. Check fast! BMS Link: {url}")
        except requests.exceptions.RequestException as e:
            print(f"Request error for {label}: {e}")
        except Exception as e:
            print(f"Parsing error for {label}: {e}")

    return found_shows

if __name__ == "__main__":
    new_shows = check_for_shows()
    if new_shows:
        message = "\n".join(new_shows)
        send_telegram_message(message)
        print("Notification sent and script is finished.")
    else:
        print("No new Japanese 4DX shows found.")

