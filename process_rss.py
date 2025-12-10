import sys
import feedparser
import html
from datetime import datetime
import socket

def fetch_and_process_rss(daily_content, urls):
    """
    Fetches RSS feeds, processes them, and returns an HTML string with the provided daily_content.
    """
    # Set a global timeout for all socket operations to prevent indefinite hanging.
    socket.setdefaulttimeout(30)
    
    # Set a realistic User-Agent to avoid being blocked by servers.
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

    date_str = datetime.now().strftime('%Y-%m-%d')
    html_content = f"<html><head><meta charset=\"UTF-8\"><title>CBC News Headlines</title></head><body><h1>CBC News Headlines - {date_str}</h1>"
    
    # Insert the daily content snippet
    if daily_content:
        html_content += daily_content

    for url in urls:
        try:
            feed = feedparser.parse(url, agent=user_agent)
            # Check if the feed was parsed correctly
            if feed.bozo:
                raise feed.bozo_exception
            
            feed_title = feed.feed.title
            html_content += f"<h2>{html.escape(feed_title)}</h2><ul>"

            for i, entry in enumerate(feed.entries):
                if i >= 9:
                    break
                
                title = html.escape(entry.title)
                link = entry.link
                description = entry.summary

                html_content += f"<li><a href='{link}'>{title}</a><p>{description}</p></li>"
            
            html_content += "</ul>"
        except Exception as e:
            print(f"Error processing feed {url}: {e}", file=sys.stderr)

    html_content += "</body></html>"
    return html_content

if __name__ == "__main__":
    if len(sys.argv) > 2:
        # The first argument is the daily content, the rest are RSS URLs
        daily_content_snippet = sys.argv[1]
        rss_urls = sys.argv[2:]
        processed_html = fetch_and_process_rss(daily_content_snippet, rss_urls)
        print(processed_html)
    elif len(sys.argv) > 1:
        # Fallback for when no daily content is provided
        rss_urls = sys.argv[1:]
        processed_html = fetch_and_process_rss("", rss_urls)
        print(processed_html)
    else:
        print("Usage: python process_rss.py [daily_content] <url1> <url2> ...", file=sys.stderr)
        sys.exit(1)
