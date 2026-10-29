const fs = require('fs');
(async () => {
  try {
    const r = await fetch('https://api.github.com/repos/OPEXAI-GG/fpl-ai-agent/releases/assets/678256795', {
      headers: {Authorization: 'Bearer ' + process.env.GITHUB_PERSONAL_ACCESS_TOKEN, Accept: 'application/octet-stream', 'User-Agent': 'FAA-7'},
      signal: AbortSignal.timeout(45000)
    });
    console.log('HTTP', r.status, 'type', r.headers.get('content-type'));
    if (r.ok && !r.headers.get('content-type').includes('json')) {
      const {Readable} = require('stream');
      const {pipeline} = require('stream/promises');
      await pipeline(Readable.fromWeb(r.body), fs.createWriteStream('artifacts/FAA-7-production/football-db.tar.zst'));
      console.log('saved');
    }
  } catch(e) { console.log(e.name, e.message, e.cause?.code); }
})();
