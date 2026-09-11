import fs from 'node:fs/promises';
import path from 'node:path';
import { PlaywrightCrawler, Dataset, log } from 'crawlee';

function normalizeStartUrls(input) {
  const raw = input.startUrls ?? input.urls ?? [];
  const list = Array.isArray(raw) ? raw : [raw];
  return list
    .map((item) => (typeof item === 'string' ? item : item?.url))
    .filter(Boolean)
    .map((url) => ({ url }));
}

export async function run(input, context) {
  const startUrls = normalizeStartUrls(input);
  if (!startUrls.length) throw new Error('web-scout requires input.startUrls or input.urls');

  const maxRequestsPerCrawl = Math.min(Math.max(Number(input.maxRequestsPerCrawl ?? 20), 1), 200);
  const maxDepth = Math.min(Math.max(Number(input.maxDepth ?? 0), 0), 5);
  const sameDomain = input.sameDomain !== false;
  const includeLinks = input.includeLinks === true;
  const textLimit = Math.min(Math.max(Number(input.textLimit ?? 12000), 500), 50000);
  const allowedHosts = new Set(startUrls.map(({ url }) => new URL(url).hostname));

  const dataset = await Dataset.open(`run-${Date.now()}`);

  const crawler = new PlaywrightCrawler({
    maxRequestsPerCrawl,
    maxConcurrency: Math.min(Math.max(Number(input.maxConcurrency ?? 3), 1), 10),
    requestHandlerTimeoutSecs: Math.min(Math.max(Number(input.timeoutSecs ?? 60), 10), 300),
    launchContext: { launchOptions: { headless: true } },

    async requestHandler({ request, page, enqueueLinks }) {
      log.info(`Scraping ${request.url}`);

      if (input.waitForSelector) {
        await page.waitForSelector(String(input.waitForSelector), {
          timeout: Math.min(Number(input.waitForSelectorTimeoutMs ?? 10000), 30000),
        }).catch(() => {});
      }

      const data = await page.evaluate(({ includeLinks, textLimit }) => {
        const links = includeLinks
          ? Array.from(document.querySelectorAll('a[href]')).slice(0, 500).map((a) => ({
              text: (a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 300),
              href: a.href,
            }))
          : undefined;

        return {
          title: document.title || '',
          description: document.querySelector('meta[name="description"]')?.getAttribute('content') || '',
          h1: Array.from(document.querySelectorAll('h1'))
            .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim())
            .filter(Boolean)
            .slice(0, 20),
          text: (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, textLimit),
          links,
        };
      }, { includeLinks, textLimit });

      await dataset.pushData({
        url: request.url,
        loadedUrl: request.loadedUrl,
        depth: request.userData?.depth ?? 0,
        scrapedAt: new Date().toISOString(),
        title: data.title,
        description: data.description,
        h1: data.h1,
        text: data.text,
        ...(includeLinks ? { links: data.links } : {}),
      });

      const depth = Number(request.userData?.depth ?? 0);
      if (depth < maxDepth) {
        await enqueueLinks({
          strategy: sameDomain ? 'same-hostname' : 'all',
          transformRequestFunction: (req) => {
            const host = new URL(req.url).hostname;
            if (sameDomain && !allowedHosts.has(host)) return false;
            req.userData = { ...(req.userData || {}), depth: depth + 1 };
            return req;
          },
        });
      }
    },
  });

  await crawler.run(startUrls);
  const { items } = await dataset.getData({ limit: 1000 });

  await fs.writeFile(
    path.join(context.outputDir, 'dataset.json'),
    JSON.stringify(items, null, 2),
    'utf8',
  );

  const sample = items.slice(0, 10);
  const summary = [
    `**Actor:** \`${context.actorName}\``,
    `**Pages scraped:** ${items.length}`,
    '',
    '#### Sample',
    '',
    ...sample.flatMap((item, i) => [
      `${i + 1}. **${String(item.title || '(no title)').replace(/\n/g, ' ')}**`,
      `   ${item.url}`,
    ]),
  ];

  if (items.length > sample.length) {
    summary.push('', `Full dataset contains ${items.length} rows and is attached to the Actions run.`);
  }

  await fs.writeFile(path.join(context.outputDir, 'summary.md'), summary.join('\n'), 'utf8');

  return {
    actor: context.actorName,
    pagesScraped: items.length,
    datasetFile: 'dataset.json',
  };
}
