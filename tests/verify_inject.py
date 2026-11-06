#!/usr/bin/env python3
"""Verify table injection: serve repo, route-intercept index.html with the v9.6
table, then read back window.OVERWORLD_DMG_PATCHES from the live page."""
import asyncio, re, subprocess, sys, time

REPO = '/home/z/my-project/drweb'
PORT = 8901

def get_v96_table():
    r = subprocess.run(['git', '-C', REPO, 'show', '95522d8:index.html'],
                       capture_output=True, text=True)
    m = re.search(r'window\.OVERWORLD_DMG_PATCHES = (\{.*?\});', r.stdout, re.S)
    return m.group(1)

async def main():
    from playwright.async_api import async_playwright
    table = get_v96_table()
    print('v9.6 table starts:', table[:80])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            '--enable-unsafe-swiftshader', '--no-sandbox', '--disable-dev-shm-usage'])
        ctx = await browser.new_context(service_workers='block')
        replaced = [False]

        async def handler(route):
            resp = await route.fetch()
            body = await resp.text()
            ph = 'window.OVERWORLD_DMG_PATCHES = {};'
            if ph in body:
                body = body.replace(ph, f'window.OVERWORLD_DMG_PATCHES = {table};')
                replaced[0] = True
            await route.fulfill(status=resp.status, headers=dict(resp.headers), body=body)

        await ctx.route(f'http://127.0.0.1:{PORT}/index.html*', handler)
        await ctx.route(f'http://127.0.0.1:{PORT}/', handler)
        page = await ctx.new_page()
        await page.goto(f'http://127.0.0.1:{PORT}/index.html?c=2&hm=damage',
                        wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        got = await page.evaluate('JSON.stringify(Object.keys(window.OVERWORLD_DMG_PATCHES || {}))')
        ch2 = await page.evaluate('(window.OVERWORLD_DMG_PATCHES["2"]||[]).length')
        hm = await page.evaluate('localStorage.getItem("dr_webport_hardmode")')
        print('replaced:', replaced[0])
        print('page table keys:', got, 'ch2 entries:', ch2)
        await browser.close()

asyncio.run(main())
