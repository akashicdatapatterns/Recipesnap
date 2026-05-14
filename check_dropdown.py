
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        url = "http://localhost:8501"
        try:
            await page.goto(url)
            # Use specific placeholder or label
            await page.get_by_label("Username or Email").first.fill("admin")
            await page.get_by_label("Password").first.fill("admin123")
            await page.get_by_role("button", name="Log in").first.click()
            await page.wait_for_timeout(5000)
            
            # Print page text to see what is visible
            text = await page.content()
            print("--- PAGE CONTENT START ---")
            # Just print a bit of it or look for "Selected"
            import re
            selected_matches = re.findall(r"Selected\s*:\s*[^\n<]+", text)
            print(selected_matches)
            print("--- PAGE CONTENT END ---")

            selectboxes = await page.get_by_role("combobox").all()
            for i, box in enumerate(selectboxes):
                value = await box.inner_text()
                print(f"Selectbox {i} text: {value}")
                
            await page.locator("[data-testid=\"stSidebar\"]").screenshot(path="sidebar.png")
            print("Screenshot saved.")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser.close()

asyncio.run(main())

