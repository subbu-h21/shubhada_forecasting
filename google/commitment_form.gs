/**
 * Shubhada Health - staff commitment form (Google Apps Script).
 *
 * Run this ONCE to create the Google Form and its answers sheet in your own
 * Google Drive:
 *   1. Open https://script.google.com and click "New project".
 *   2. Delete what is there, paste this whole file, click Save.
 *   3. Choose createCommitmentForm at the top and click Run. Allow access
 *      when Google asks (it is your own account creating your own form).
 *   4. Open "Execution log" at the bottom. It shows three links:
 *        FORM LINK      - the form itself
 *        ANSWERS SHEET  - where every answer lands
 *        TEMPLATE LINK  - paste this into the reckoner: People > Salary >
 *                         Setup > Commitment links. The reckoner then makes
 *                         each person's own WhatsApp link with their name,
 *                         usual timings and best-month targets filled in.
 *
 * The template link carries placeholder values (__NAME__, 01:01, 90001 ...)
 * that the reckoner swaps for each person's own values - do not edit them.
 */
function createCommitmentForm() {
  var form = FormApp.create('Shubhada Health - My Commitment / ನನ್ನ ಬದ್ಧತೆ');
  form.setDescription(
    'Please fill this in yourself. It records the timings you agree to and the targets you choose to chase. ' +
    'Your salary slip is judged against what you commit here.\n' +
    'ದಯವಿಟ್ಟು ನೀವೇ ತುಂಬಿ. ನೀವು ಒಪ್ಪುವ ಸಮಯ ಮತ್ತು ನೀವು ಆಯ್ಕೆ ಮಾಡುವ ಗುರಿಯನ್ನು ಇದು ದಾಖಲಿಸುತ್ತದೆ.');
  form.setCollectEmail(false);
  form.setAllowResponseEdits(false);
  form.setProgressBar(false);
  form.setConfirmationMessage(
    'Thank you - your commitment is recorded. The owner will confirm it.\n' +
    'ಧನ್ಯವಾದಗಳು - ನಿಮ್ಮ ಬದ್ಧತೆ ದಾಖಲಾಗಿದೆ.');

  var time = FormApp.createTextValidation()
    .requireTextMatchesPattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')
    .setHelpText('Write the time like 09:30 or 17:00 / ಸಮಯವನ್ನು 09:30 ಹೀಗೆ ಬರೆಯಿರಿ').build();
  var number = FormApp.createTextValidation()
    .requireNumberGreaterThanOrEqualTo(0)
    .setHelpText('Numbers only / ಸಂಖ್ಯೆ ಮಾತ್ರ').build();

  var it = {};
  it.name = form.addTextItem().setTitle('Your name / ನಿಮ್ಮ ಹೆಸರು').setRequired(true);
  it.shift = form.addMultipleChoiceItem().setTitle('Your shift / ನಿಮ್ಮ ಪಾಳಿ')
    .setChoiceValues(['Full day / ಪೂರ್ಣ ದಿನ', 'Half day / ಅರ್ಧ ದಿನ']).setRequired(true);

  form.addSectionHeaderItem().setTitle('My timings / ನನ್ನ ಸಮಯ')
    .setHelpText('Your usual times from the bills are filled in. Change them to the times you agree to keep. ' +
                 'Half day: leave both break boxes empty.\n' +
                 'ಬಿಲ್‌ಗಳಿಂದ ನಿಮ್ಮ ಸಾಮಾನ್ಯ ಸಮಯ ತುಂಬಲಾಗಿದೆ. ನೀವು ಪಾಲಿಸುವ ಸಮಯಕ್ಕೆ ಬದಲಾಯಿಸಿ.');
  it.start = form.addTextItem().setTitle('Start time / ಬರುವ ಸಮಯ').setValidation(time).setRequired(true);
  it.breakOut = form.addTextItem().setTitle('Break starts / ವಿರಾಮ ಆರಂಭ').setValidation(time);
  it.breakIn = form.addTextItem().setTitle('Back from break / ವಿರಾಮದಿಂದ ಮರಳುವುದು').setValidation(time);
  it.end = form.addTextItem().setTitle('End time / ಹೋಗುವ ಸಮಯ').setValidation(time).setRequired(true);
  it.off = form.addListItem().setTitle('Weekly off day / ವಾರದ ರಜೆ')
    .setChoiceValues(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday', 'No fixed day'])
    .setRequired(true);

  form.addSectionHeaderItem().setTitle('My targets / ನನ್ನ ಗುರಿ')
    .setHelpText('Your best month so far is filled in. Keep it or raise it - reaching the target you choose earns a bonus. ' +
                 'Leave a box empty if it is not your work.\n' +
                 'ನಿಮ್ಮ ಉತ್ತಮ ತಿಂಗಳು ತುಂಬಲಾಗಿದೆ. ಅದನ್ನೇ ಇಡಿ ಅಥವಾ ಹೆಚ್ಚಿಸಿ - ತಲುಪಿದರೆ ಬೋನಸ್.');
  it.tSales = form.addTextItem().setTitle('Sales per day (Rs) / ದಿನಕ್ಕೆ ಮಾರಾಟ (ರೂ)').setValidation(number);
  it.tBills = form.addTextItem().setTitle('Bills per day / ದಿನಕ್ಕೆ ಬಿಲ್‌ಗಳು').setValidation(number);
  it.tLines = form.addTextItem().setTitle('Items fetched per day / ದಿನಕ್ಕೆ ತಂದ ಐಟಂಗಳು').setValidation(number);
  it.tProducts = form.addTextItem().setTitle('Different products fetched in a month / ತಿಂಗಳಲ್ಲಿ ಬೇರೆ ಬೇರೆ ಉತ್ಪನ್ನಗಳು').setValidation(number);
  it.tEntry = form.addTextItem().setTitle('Purchase lines entered per day / ದಿನಕ್ಕೆ ಖರೀದಿ ನಮೂದು').setValidation(number);

  it.promise = form.addCheckboxItem().setTitle('I promise / ನಾನು ಭರವಸೆ ನೀಡುತ್ತೇನೆ')
    .setChoiceValues([
      'I will come on time every day / ಪ್ರತಿದಿನ ಸಮಯಕ್ಕೆ ಬರುತ್ತೇನೆ',
      'I will inform before taking leave / ರಜೆ ಮೊದಲು ತಿಳಿಸುತ್ತೇನೆ',
      'I will check every item before giving it / ಕೊಡುವ ಮೊದಲು ಪ್ರತಿ ಐಟಂ ಪರಿಶೀಲಿಸುತ್ತೇನೆ',
      'I will keep my racks clean and in order / ನನ್ನ ರ್ಯಾಕ್ ಸ್ವಚ್ಛವಾಗಿ ಇಡುತ್ತೇನೆ'])
    .setValidation(FormApp.createCheckboxValidation().requireSelectExactly(4)
      .setHelpText('Tick all four / ನಾಲ್ಕನ್ನೂ ಆಯ್ಕೆ ಮಾಡಿ').build())
    .setRequired(true);
  it.sign = form.addTextItem().setTitle('Type your full name to sign / ಸಹಿಗಾಗಿ ನಿಮ್ಮ ಪೂರ್ಣ ಹೆಸರು ಬರೆಯಿರಿ')
    .setRequired(true);

  var sheet = SpreadsheetApp.create('Shubhada Health - Commitments (answers)');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());

  // Template link: placeholder values the reckoner replaces per person.
  var r = form.createResponse()
    .withItemResponse(it.name.createResponse('__NAME__'))
    .withItemResponse(it.shift.createResponse('Full day / ಪೂರ್ಣ ದಿನ'))
    .withItemResponse(it.start.createResponse('01:01'))
    .withItemResponse(it.breakOut.createResponse('02:02'))
    .withItemResponse(it.breakIn.createResponse('03:03'))
    .withItemResponse(it.end.createResponse('04:04'))
    .withItemResponse(it.tSales.createResponse('90001'))
    .withItemResponse(it.tBills.createResponse('90002'))
    .withItemResponse(it.tLines.createResponse('90003'))
    .withItemResponse(it.tProducts.createResponse('90004'))
    .withItemResponse(it.tEntry.createResponse('90005'));

  Logger.log('FORM LINK:     ' + form.getPublishedUrl());
  Logger.log('ANSWERS SHEET: ' + sheet.getUrl());
  Logger.log('TEMPLATE LINK: ' + r.toPrefilledUrl());
}
