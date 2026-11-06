/* ApplicationTrackr Sheet writer. Deploy as a new version of the existing web app.
 * Uses its bound spreadsheet and named Raw tab. It never changes sharing/access.
 * Keep normalization contracts aligned with core/stages.py and core/normalization.py.
 */
function trackrText(value) {
  return String(value || '').replace(/&amp;/gi, '&').replace(/&#(?:0*39|x0*27);|&apos;/gi, "'")
    .replace(/&quot;|&#0*34;/gi, '"').replace(/&nbsp;|&#0*160;/gi, ' ').trim().replace(/\s+/g, ' ');
}
function trackrRole(value) {
  return trackrText(value).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}
function trackrCompany(value) {
  var key = trackrText(value).toLowerCase().replace(/[^a-z0-9]/g, '');
  ['ltd','inc','plc','llc','capital','technologies','technology','group','uk','europe','limited','careers','jobs','program','internshipprogram'].forEach(function(suffix) {
    if (key.endsWith(suffix) && key.length > suffix.length + 2) key = key.slice(0, -suffix.length);
  });
  return TRACKR_COMPANY_ALIASES[key] || key;
}
function trackrStage(value) {
  var text = trackrText(value), match = text.match(/^(.+?)\s+(?:round\s+)?([1-9][0-9]?)$/i);
  var base = match ? match[1] : text, number = match ? Number(match[2]) : null;
  var name = TRACKR_STAGE_ALIASES[base.toLowerCase()] || base;
  return name + (number === null ? '' : ' ' + number);
}
function trackrStageKey(value) {
  var stage = trackrStage(value), match = stage.match(/^(.+) ([1-9][0-9]?)$/);
  return match && TRACKR_ROUND_STAGES.indexOf(match[1]) >= 0
    ? match[1].toLowerCase() + ':' + Number(match[2]) : stage.toLowerCase() + ':1';
}
function trackrHasStage(stage, stages) {
  stage = trackrStage(stage);
  if (stage === 'Reopened') return stages.length > 0 && trackrStage(stages[stages.length-1]) === 'Reopened';
  var reopened = -1;
  stages.forEach(function(old,i) { if (trackrStage(old) === 'Reopened') reopened = i; });
  if (reopened >= 0) stages = stages.slice(reopened + 1);
  return stages.some(function(old) {
    if (trackrStageKey(stage) === trackrStageKey(old)) return true;
    if (TRACKR_ROUND_STAGES.indexOf(stage) < 0) return false;
    return trackrStageKey(old).split(':')[0] === stage.toLowerCase()
      || (stage === 'Interview' && trackrStageKey(old).indexOf('interview') >= 0);
  });
}
function trackrPhase(stage) {
  stage = trackrStage(stage).toLowerCase();
  if (/reject|fail|offer|withdraw|ghost/.test(stage)) return 3;
  if (/interview|phone screen|group assessment|assessment centre/.test(stage)) return 2;
  if (/assessment|test/.test(stage) || stage === 'oa') return 1;
  return 0;
}
function trackrConflict(stage, stages) {
  if (!stages.length || trackrHasStage(stage, stages)) return false;
  var reopened = -1;
  stages.forEach(function(old,i) { if (trackrStage(old) === 'Reopened') reopened = i; });
  if (reopened >= 0) stages = stages.slice(reopened);
  var latestBase = trackrStage(stages[stages.length-1]).replace(/ [1-9][0-9]?$/, '');
  if (!TRACKR_STAGE_ALIASES[latestBase.toLowerCase()]) return true;
  if (trackrPhase(stages[stages.length - 1]) === 3 || trackrPhase(stage) < trackrPhase(stages[stages.length - 1])) return true;
  var key = trackrStageKey(stage).split(':');
  return stages.some(function(old) { var other = trackrStageKey(old).split(':'); return other[0] === key[0] && Number(other[1]) > Number(key[1]); });
}
function trackrResult(value) {
  value.writer_version = 2;
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
function doGet() { return trackrResult({status:'ok'}); }
function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(25000);
    var data = JSON.parse(e.postData.contents || '{}');
    var company = trackrText(data.company), role = trackrText(data.role), stage = trackrStage(data.stage);
    if (!company || !role || !stage || company === 'Application Company'
        || trackrCompany(company) === trackrCompany(role)
        || [company,role,stage].some(function(text) { return /^[=+\-@]/.test(text); })
        || /[\x00-\x1f\x7f<>]/.test(stage) || stage.length > 80) {
      return trackrResult({status:'error', error:'A specific company, role and plain-text stage are required.'});
    }
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Raw');
    if (!sheet) return trackrResult({status:'error', error:'Raw tab not found.'});
    var rows = sheet.getDataRange().getValues(), header = rows[0] || [];
    if (header[0] !== 'Company' || header[1] !== 'Role') return trackrResult({status:'error', error:'Company/Role headers missing.'});
    var columns = [];
    header.forEach(function(value, index) { var m = String(value).match(/^Stage\s*(\d+)$/i); if (m) columns.push({index:index,number:Number(m[1])}); });
    columns.sort(function(a,b) { return a.number - b.number; });
    if (!columns.length) return trackrResult({status:'error', error:'Stage columns missing.'});
    var matches = [];
    for (var row = 1; row < rows.length; row++) {
      if (trackrCompany(rows[row][0]) === trackrCompany(company) && trackrRole(rows[row][1]) === trackrRole(role)) matches.push(row);
    }
    if (matches.length > 1) return trackrResult({status:'error', error:'Duplicate company/role rows need review; no row was changed.'});
    var found = matches.length ? matches[0] : -1;
    var stages = found < 0 ? [] : columns.map(function(c) { return rows[found][c.index]; }).filter(function(v) { return trackrText(v); });
    if (trackrHasStage(stage, stages)) return trackrResult({status:'duplicate', stage:stage});
    if (data.automatic === true && trackrConflict(stage, stages)) return trackrResult({status:'error', error:'Email conflicts with the current stage; review required.'});
    var target;
    if (found < 0) {
      found = Math.max(1, sheet.getLastRow());
      var newRow = new Array(header.length).fill('');
      newRow[0] = company; newRow[1] = role;
      target = columns[0].index;
      sheet.getRange(found + 1, 1, 1, newRow.length).setValues([newRow]);
    } else {
      // Append after the last used stage; a manually cleared earlier cell is not a new slot.
      var lastUsed = -1;
      columns.forEach(function(c,i) { if (trackrText(rows[found][c.index])) lastUsed = i; });
      if (lastUsed + 1 < columns.length) target = columns[lastUsed + 1].index;
      else {
        target = sheet.getLastColumn();
        if (target >= sheet.getMaxColumns()) sheet.insertColumnAfter(target);
        sheet.getRange(1, target + 1).setValue('Stage ' + (columns[columns.length-1].number + 1));
      }
    }
    var cell = sheet.getRange(found + 1, target + 1);
    cell.setValue(stage);
    if (data.event_id && /^[a-f0-9]{64}$/.test(data.event_id)) cell.setNote('ApplicationTrackr event: ' + data.event_id);
    SpreadsheetApp.flush();
    return trackrResult({status:'success', stage:stage, row:found+1});
  } catch (error) {
    return trackrResult({status:'error', error:String(error.message || error)});
  } finally {
    if (lock.hasLock()) lock.releaseLock();
  }
}

// Shared identity vocabulary (contract-tested against the Python writer).
var TRACKR_COMPANY_ALIASES = {"98cd1a0027064aa8ab7238a7b8c9c20c": "snyk", "bankofnewyorkmellon": "bny", "beamng": "beamng", "bnymellon": "bny", "canonical": "canonical", "canonicaljobs": "canonical", "cohere": "cohere", "hrt": "hrt", "hudsonrivertrading": "hrt", "imctrading": "imc", "janestreet": "janestreet", "jumptrading": "jumptrading", "marshallwace": "marshallwace", "mistralai": "mistral", "mizuhobanking": "mizuho", "mw": "marshallwace", "mwinternshipprogram": "marshallwace", "notion": "notion", "optiver": "optiver", "palantir": "palantir", "ramp": "ramp", "samsara": "samsara", "squarepoint": "squarepoint", "squarepointcapital": "squarepoint", "starling": "starling", "starlingbank": "starling", "thephoenixpartnership": "tpp", "ttp1": "ttp", "twosigma": "twosigma", "twosigmaca": "twosigma", "wayve": "wayve"};
var TRACKR_STAGE_ALIASES = {"accepted": "Offer Accepted", "application received": "Applied", "application submitted": "Applied", "applied": "Applied", "assessment": "Online Assessment", "assessment center": "Assessment Centre", "assessment centre": "Assessment Centre", "digital interview": "Digital Interview", "final interview": "Final Interview", "ghosted": "Ghosted", "group assessment": "Group Assessment", "group exercise": "Group Assessment", "interview": "Interview", "oa": "Online Assessment", "offer": "Offer", "offer accepted": "Offer Accepted", "online assessment": "Online Assessment", "phone interview": "Phone Screen", "phone screen": "Phone Screen", "reject": "Rejected", "rejected": "Rejected", "reopened": "Reopened", "technical interview": "Technical Interview", "withdrawn": "Withdrawn"};
var TRACKR_ROUND_STAGES = ["Assessment Centre", "Digital Interview", "Final Interview", "Group Assessment", "Interview", "Online Assessment", "Phone Screen", "Technical Interview"];
