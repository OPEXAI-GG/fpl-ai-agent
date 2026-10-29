const fs = require('fs');
const s = fs.readFileSync('C:/Program Files/Munder Difflin/resources/app.asar').toString();
let i=s.indexOf('const entry = \'sandbox = "unelevated"\'');
console.log(s.slice(i-5400,i+3300));
