import fs from 'node:fs/promises';
import path from 'node:path';

const IG_APP_ID = '936619743392459';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';

function cleanUsername(value) {
  return String(value || '')
    .trim()
    .replace(/^https?:\/\/(www\.)?instagram\.com\//i, '')
    .replace(/^@/, '')
    .split(/[/?#]/)[0]
    .trim();
}

async function fetchProfile(username) {
  const url = `https://www.instagram.com/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`;
  const response = await fetch(url, {
    headers: {
      'accept': '*/*',
      'accept-language': 'en-US,en;q=0.9',
      'user-agent': UA,
      'x-ig-app-id': IG_APP_ID,
      'x-requested-with': 'XMLHttpRequest',
      'referer': `https://www.instagram.com/${encodeURIComponent(username)}/`,
    },
    redirect: 'follow',
  });

  const text = await response.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}

  if (!response.ok) {
    return { username, ok: false, status: response.status, error: json?.message || text.slice(0, 300) || 'HTTP error' };
  }

  const user = json?.data?.user;
  if (!user) return { username, ok: false, status: response.status, error: 'No user object returned' };

  return {
    username: user.username,
    fullName: user.full_name || '',
    biography: user.biography || '',
    followers: user.edge_followed_by?.count ?? null,
    following: user.edge_follow?.count ?? null,
    posts: user.edge_owner_to_timeline_media?.count ?? null,
    isPrivate: Boolean(user.is_private),
    isVerified: Boolean(user.is_verified),
    isBusinessAccount: Boolean(user.is_business_account),
    isProfessionalAccount: Boolean(user.is_professional_account),
    businessCategoryName: user.business_category_name || '',
    categoryName: user.category_name || '',
    externalUrl: user.external_url || '',
    profileUrl: `https://www.instagram.com/${user.username}/`,
    ok: true,
    status: response.status,
  };
}

export async function run(input, context) {
  const values = Array.isArray(input.usernames) ? input.usernames : [];
  if (!values.length) throw new Error('instagram-profile-check requires input.usernames');

  const usernames = [...new Set(values.map(cleanUsername).filter(Boolean))].slice(0, 200);
  const concurrency = Math.min(Math.max(Number(input.concurrency ?? 2), 1), 5);
  const results = new Array(usernames.length);
  let next = 0;

  async function worker() {
    while (true) {
      const i = next++;
      if (i >= usernames.length) return;
      const username = usernames[i];
      try {
        results[i] = await fetchProfile(username);
      } catch (error) {
        results[i] = { username, ok: false, error: error instanceof Error ? error.message : String(error) };
      }
      await new Promise((resolve) => setTimeout(resolve, Number(input.delayMs ?? 900)));
    }
  }

  await Promise.all(Array.from({ length: concurrency }, () => worker()));

  await fs.mkdir(context.outputDir, { recursive: true });
  await fs.writeFile(path.join(context.outputDir, 'dataset.json'), JSON.stringify(results, null, 2), 'utf8');

  const successful = results.filter((r) => r?.ok);
  const publicProfiles = successful.filter((r) => !r.isPrivate);
  const summary = [
    `**Actor:** \`${context.actorName}\``,
    `**Profiles requested:** ${usernames.length}`,
    `**Resolved by Instagram:** ${successful.length}`,
    `**Public profiles:** ${publicProfiles.length}`,
    '',
    '#### Resolved public profiles',
    '',
    ...publicProfiles.flatMap((r, i) => [
      `${i + 1}. **@${r.username} | ${r.fullName || '(no name)'}**`,
      `   ${r.profileUrl}`,
      `   Bio: ${String(r.biography || '(empty)').replace(/\s+/g, ' ').slice(0, 600)}`,
      `   Category: ${r.categoryName || r.businessCategoryName || '(none)'} | Professional: ${r.isProfessionalAccount} | Business: ${r.isBusinessAccount} | Followers: ${r.followers ?? 'n/a'} | Posts: ${r.posts ?? 'n/a'}`,
    ]),
    '',
    '#### Unresolved/private',
    ...results.filter((r) => !r?.ok || r.isPrivate).slice(0, 100).map((r) => `- @${r.username}: ${r.isPrivate ? 'private' : (r.error || `HTTP ${r.status || '?'}`)}`),
  ];

  await fs.writeFile(path.join(context.outputDir, 'summary.md'), summary.join('\n'), 'utf8');

  return {
    requested: usernames.length,
    resolved: successful.length,
    publicProfiles: publicProfiles.length,
  };
}
