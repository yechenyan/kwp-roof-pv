/* Offline rendering of frozen predictions; no inference or external requests. */
const data = window.BENCHMARK;
const names = { a1_deeplab: 'DeepLab IGN', a2_segformer: 'SegFormer IGN', a3_brandenburg: 'Brandenburg RGB', a3_brandenburg_rgbi: 'Brandenburg RGBI', a4_yolo: 'YOLOv8s PV', a5_maskrcnn: 'Mask R-CNN', a6_sam3: 'SAM 3 (blocked)', a7_gpt_sol: 'GPT-6.1 Sol (historical)', a8_gpt_terra: 'GPT-6.1 Terra (historical)' };
const score = value => typeof value === 'number' ? value.toFixed(4) : 'N/A';
const sorted = [...data.entrants].sort((a,b) => (b.objects_50?.f1 ?? -1) - (a.objects_50?.f1 ?? -1));
for (const entry of sorted) {
  const row = document.createElement('tr');
  const object = entry.objects_50;
  const cells = [names[entry.entrant_id], entry.track === 'enhanced' ? 'RGB + NIR' : 'RGB', object ? `${object.tp} / ${object.fp} / ${object.fn}` : 'Blocked', score(object?.precision), score(object?.recall), score(object?.f1), score(entry.pixels?.iou), typeof entry.area_mae_m2 === 'number' ? entry.area_mae_m2.toFixed(2) : 'N/A'];
  for (const value of cells) { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }
  document.querySelector('#scores').append(row);
  const detail = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = names[entry.entrant_id]; detail.append(summary);
  const pre = document.createElement('pre'); pre.textContent = JSON.stringify({ model: entry.model, weights: entry.weights, preprocessing: entry.preprocessing, thresholds: entry.thresholds, limitations: entry.limitations }, null, 2); detail.append(pre);
  document.querySelector('#provenance').append(detail);
}
for (const id of ['left','right']) for (const entry of sorted) {
  const option = document.createElement('option'); option.value = entry.entrant_id; option.textContent = names[entry.entrant_id]; document.getElementById(id).append(option);
}
for (const image of data.images) { const option = document.createElement('option'); option.value = image.id; option.textContent = image.id; document.querySelector('#image').append(option); }
document.querySelector('#left').value = 'a3_brandenburg_rgbi'; document.querySelector('#right').value = 'a3_brandenburg';
const load = path => new Promise((resolve,reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error(`Unable to load ${path}`)); image.src = path; });
async function overlay(context, filename, color) {
  const mask = await load(`assets/${filename}`); const offscreen = document.createElement('canvas'); offscreen.width = 800; offscreen.height = 800; const ctx = offscreen.getContext('2d'); ctx.drawImage(mask,0,0); const pixels = ctx.getImageData(0,0,800,800);
  for (let index=0;index<pixels.data.length;index+=4) { const present = pixels.data[index] > 0; pixels.data[index]=color[0]; pixels.data[index+1]=color[1]; pixels.data[index+2]=color[2]; pixels.data[index+3]=present ? 115 : 0; }
  ctx.putImageData(pixels,0,0); context.drawImage(offscreen,0,0);
}
let generation = 0;
async function draw() {
  const ticket = ++generation; const image = data.images.find(image => image.id === document.querySelector('#image').value);
  document.querySelector('#view-error').textContent = '';
  try {
    const base = await load(`assets/${image.id}.png`);
    for (const side of ['left','right']) {
      const model = document.getElementById(side).value; const record = image.records[model];
      const offscreen = document.createElement('canvas'); offscreen.width=800; offscreen.height=800; const context=offscreen.getContext('2d'); context.drawImage(base,0,0);
      if (record?.mask) await overlay(context,record.mask,[255,176,63]);
      else if (record?.status === 'completed') { context.strokeStyle='#ffc354'; context.lineWidth=3; for (const [x1,y1,x2,y2] of record.boxes) context.strokeRect(x1,y1,x2-x1,y2-y1); }
      if (document.querySelector('#reference').checked) await overlay(context,`${image.id}_reference.png`,[29,244,146]);
      if (ticket !== generation) return;
      document.getElementById(`${side}-canvas`).getContext('2d').drawImage(offscreen,0,0);
      document.getElementById(`${side}-label`).textContent = `${names[model]} · ${record?.mask ? 'mask' : record?.status === 'completed' ? 'boxes' : 'no inference'}`;
    }
  } catch (error) { if(ticket === generation) document.querySelector('#view-error').textContent=String(error); }
}
document.querySelectorAll('select,input').forEach(control => control.addEventListener('change',draw)); draw();
