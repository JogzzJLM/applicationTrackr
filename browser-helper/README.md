# Safari form helper

Click **Open & fill** on a job in ApplicationTrackr. The helper opens the actual employer application in a new Safari tab, fills matching saved details and uploaded documents, and leaves unanswered questions and submission to you. It does not click Apply, Submit, Next, or accept agreements.

The private profile and CV are fetched from `http://192.168.0.136:5000` only when you open a job. Only that job's answers are included. Persistent extension storage contains pending job identifiers and URLs, never your profile or CV. Existing answers are preserved. Unsupported sites open for manual completion.

## Build and enable

On a Mac with Xcode, run `bash browser-helper/build-safari.sh`. Builds are created outside this source folder to avoid the converter copying its own output. Open the resulting **ApplicationTrackr Helper.app**, then enable **ApplicationTrackr — Fill Job Forms** in Safari Settings → Extensions. The installed helper is saved in `~/Applications/ApplicationTrackr Helper.app`. A local ad-hoc build may require Safari's developer option **Allow Unsigned Extensions**; Safari may reset this option when it quits. A distributed version would need Apple developer signing.

Grant website access to ApplicationTrackr and the supported ATS pages you use: Greenhouse, Lever, Ashby and SmartRecruiters. Reload ApplicationTrackr after enabling the extension. Keep your profile and CV updated through the existing profile settings. Unknown answers entered directly on employer websites are not automatically saved or learned by this version.

Only a user-selected listing is filled. Redirects must preserve the original job path and identifier. Country-specific eligibility is reused only from an answer already saved for that particular application. Ambiguous dropdown options are left for you to choose.

## Checks

`node --test tests/browser-helper.test.cjs`

`DATA_DIR=/tmp/applicationtrackr-test-data python3 -m pytest -q`

Controlled React dropdowns use the actual available choices and verify the selection; async location lookups use the city name and require an exact saved place match. Country-specific eligibility is omitted from the profile passed to the employer page; only an explicit answer for this job can fill its work-authorisation question.
