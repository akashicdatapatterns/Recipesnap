
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        # Find the Streamlit app URL. Usually its localhost:8501
        url = "http://localhost:8501"
        try:
            await page.goto(url)
            # Login
            await page.get_by_label("Username").fill("admin")
            await page.get_by_label("Password").fill("admin123")
            await page.get_by_role("button", name="Log in").click()
            
            # Wait for main page
            await page.wait_for_timeout(5000)
            
            # Check state
            # Streamlit often uses "Selected" in accessibility names or titles
            tree = await page.accessibility.snapshot()
            print(f"Accessibility Tree: {tree}")
            
            # Screenshot of sidebar
            # Sidebar usually has [data-testid="stSidebar"]
            sidebar = page.locator("[data-testid=\"stSidebar\"]")
            await sidebar.screenshot(path="sidebar.png")
            print("Screenshot saved as sidebar.png")
            
        except Exception as e:
            print(f"Error: {e}")
            # Try to just print page content if it failed
            print(await page.content())
        finally:
            await browser.close()

asyncio.run(main())

