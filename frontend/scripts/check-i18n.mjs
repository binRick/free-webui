// Catalog-parity guard: every locale must define exactly the keys in en.json
// (the source catalog) with non-empty values — so a half-translated locale can't
// silently ship missing strings. Runs as part of `npm run check`.
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'lib', 'locales');
const en = JSON.parse(readFileSync(join(dir, 'en.json'), 'utf8'));
const enKeys = Object.keys(en);

let failed = false;
for (const file of readdirSync(dir).filter((f) => f.endsWith('.json') && f !== 'en.json')) {
  const cat = JSON.parse(readFileSync(join(dir, file), 'utf8'));
  const missing = enKeys.filter((k) => !(k in cat));
  const extra = Object.keys(cat).filter((k) => !(k in en));
  const empty = enKeys.filter((k) => k in cat && String(cat[k]).trim() === '');
  if (missing.length || extra.length || empty.length) {
    failed = true;
    console.error(`✗ ${file}`);
    if (missing.length) console.error(`    missing: ${missing.join(', ')}`);
    if (extra.length) console.error(`    extra:   ${extra.join(', ')}`);
    if (empty.length) console.error(`    empty:   ${empty.join(', ')}`);
  } else {
    console.log(`✓ ${file} (${enKeys.length} keys)`);
  }
}

if (failed) {
  console.error('\ni18n catalogs are out of parity with en.json');
  process.exit(1);
}
console.log(`i18n: ${enKeys.length} keys in parity across all locales`);
