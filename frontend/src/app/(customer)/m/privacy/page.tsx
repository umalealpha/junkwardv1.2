/** /m/privacy — Alpha Nexus privacy policy. PUBLIC (no login) — required by the
 * Apple App Store + Google Play listings and linked from the sign-in screen.
 * Version 2.0 — expanded to cover Botswana DPA 2024 and SA POPIA compliance. */
import { C, serif, sans, h, headerPad } from '../../ui'

const UPDATED = '7 September 2026'
const VERSION = '2.1'
const sec: React.CSSProperties = { ...h(19), margin: '32px 0 10px' }
const subsec: React.CSSProperties = { ...h(16), margin: '22px 0 8px' }
const p: React.CSSProperties = { fontSize: 15, lineHeight: 1.7, color: C.ink, margin: '0 0 14px' }
const li: React.CSSProperties = { fontSize: 15, lineHeight: 1.6, color: C.ink, marginBottom: 8 }
const th: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: C.ink, padding: '10px 12px', textAlign: 'left' as const, borderBottom: `2px solid ${C.line}`, background: C.surface }
const td: React.CSSProperties = { fontSize: 14, lineHeight: 1.5, color: C.ink, padding: '10px 12px', borderBottom: `1px solid ${C.line}`, verticalAlign: 'top' as const }
const table: React.CSSProperties = { width: '100%', borderCollapse: 'collapse' as const, margin: '12px 0 20px', background: C.card, borderRadius: 12, overflow: 'hidden', border: `1px solid ${C.line}` }
const bold: React.CSSProperties = { fontWeight: 700 }

export default function Privacy() {
  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>
      <main style={{ maxWidth: 760, margin: '0 auto', padding: '24px 20px 80px' }}>
        <h1 style={h(28)}>Privacy Policy</h1>
        <p style={{ ...p, color: C.inkSoft, margin: '4px 0 6px' }}>Effective Date: 1 July 2026</p>
        <p style={{ ...p, color: C.inkSoft, margin: '0 0 6px' }}>Last Updated: {UPDATED}</p>
        <p style={{ ...p, color: C.inkSoft, margin: '0 0 20px' }}>Version: {VERSION}</p>

        {/* 1. Introduction */}
        <h2 style={sec}>1. Introduction and Scope</h2>
        <p style={p}>Alpha&nbsp;Nexus is a voluntary wellness, rewards, and safe-driving application operated by <b>Alpha Direct Insurance Company (Pty) Ltd</b> (&ldquo;Alpha Direct&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;, &ldquo;our&rdquo;), a company duly registered in Botswana with its principal office in Gaborone. This Privacy Policy explains how we collect, use, store, protect, and share your personal information when you use the Alpha&nbsp;Nexus mobile application and related services (collectively, the &ldquo;App&rdquo;).</p>
        <p style={p}>This Policy is drafted in compliance with the <b>Botswana Data Protection Act 18 of 2024</b> (the &ldquo;DPA&rdquo;), which came into effect on 14 January 2025, and the <b>South African Protection of Personal Information Act 4 of 2013</b> (&ldquo;POPIA&rdquo;), as the App is available to users in both Botswana and South Africa. Where there is any conflict between the two regulatory frameworks, we apply the stricter standard.</p>
        <p style={p}>By creating an account and using the App, you acknowledge that you have read, understood, and agree to the collection and processing of your personal information as described in this Policy. If you do not agree, please do not use the App.</p>
        <p style={{ ...p, background: '#FEF9E7', padding: '12px 16px', borderRadius: 8, borderLeft: `4px solid ${C.orange}` }}><b>Important Notice:</b> Alpha&nbsp;Nexus is a standalone wellness and rewards programme. Data collected through the App is <b>not</b> used for insurance underwriting, premium calculation, claims assessment, or any actuarial purpose whatsoever.</p>
        <p style={{ ...p, fontSize: 14, color: C.inkSoft }}>Are you an Alpha&nbsp;Direct <b>insurance policyholder</b>? See our <a href="/m/privacy-notice" style={{ color: C.teal }}>Policyholder Privacy Notice</a>.</p>

        {/* 2. Data Controller */}
        <h2 style={sec}>2. Data Controller and Information Officer</h2>
        <p style={p}>The data controller responsible for your personal information is:</p>
        <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 12, padding: '16px 20px', margin: '0 0 14px' }}>
          <p style={{ ...p, margin: '0 0 4px' }}><b>Alpha Direct Insurance Company (Pty) Ltd</b></p>
          <p style={{ ...p, margin: '0 0 4px', fontSize: 14 }}>Plot 54373, Western Commercial Road</p>
          <p style={{ ...p, margin: '0 0 10px', fontSize: 14 }}>New CBD, Gaborone, Botswana</p>
          <p style={{ ...p, margin: '0 0 4px', fontSize: 14 }}><b>Information Officer:</b> <a href="mailto:privacy@alphadirect.co.bw" style={{ color: C.teal }}>privacy@alphadirect.co.bw</a></p>
          <p style={{ ...p, margin: 0, fontSize: 14 }}><b>Telephone:</b> +267 393 2377</p>
        </div>
        <p style={p}>In accordance with Section 69 of the Botswana DPA and Section 55 of POPIA, our appointed Information Officer oversees all data protection compliance matters and serves as the primary contact point for the Information and Data Protection Commission (Botswana) and the Information Regulator (South Africa).</p>

        {/* 3. Lawful Basis */}
        <h2 style={sec}>3. Lawful Basis for Processing</h2>
        <p style={p}>We process your personal information on the following lawful grounds, as required by Section 19 of the Botswana DPA and Section 11 of POPIA:</p>
        <div style={{ overflowX: 'auto' }}>
          <table style={table}>
            <thead><tr><th style={th}>Lawful Basis</th><th style={th}>Application</th></tr></thead>
            <tbody>
              <tr><td style={td}><b>Consent</b></td><td style={td}>You voluntarily create an account and opt into each feature (driving recording, wellness scans, meal submissions, and step and workout sync where available). You may withdraw consent at any time.</td></tr>
              <tr><td style={td}><b>Performance of a contract</b></td><td style={td}>Processing necessary to operate the rewards programme you have enrolled in, including calculating points, maintaining your tier status, and delivering rewards.</td></tr>
              <tr><td style={td}><b>Legitimate interest</b></td><td style={td}>Improving the App&rsquo;s functionality, detecting fraud or abuse of the rewards system, and ensuring the security of our platform.</td></tr>
              <tr><td style={td}><b>Legal obligation</b></td><td style={td}>Retaining records as required by applicable financial services regulations and responding to lawful requests from regulatory authorities.</td></tr>
            </tbody>
          </table>
        </div>

        {/* 4. Categories of Data */}
        <h2 style={sec}>4. Categories of Personal Information We Collect</h2>

        <h3 style={subsec}>4.1 Account Information</h3>
        <p style={p}>When you register, we collect your email address (used to send one-time sign-in codes for passwordless authentication) and a display name of your choosing. We do not collect your national identity number, passport number, or physical address through the App.</p>

        <h3 style={subsec}>4.2 Driving Behaviour Data (Click &amp; Drive)</h3>
        <p style={p}>When you activate the Click&nbsp;&amp;&nbsp;Drive feature and begin recording a trip, the App collects and processes the following data:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}><b>GPS location data</b> — used in real time on your device to calculate trip distance, speed, idle time, and harsh-driving events. The route you drove is never stored or transmitted.</li>
          <li style={li}><b>Speed changes derived from GPS</b> — used to detect harsh braking and rapid acceleration. The App does <b>not</b> read your phone&rsquo;s accelerometer or gyroscope and does <b>not</b> monitor phone usage while driving.</li>
          <li style={li}><b>Trip duration and timestamps</b> — start and end times of each recorded journey.</li>
          <li style={li}><b>Driving score metrics</b> — a composite safe-driving score calculated from the above inputs, including contributing factors for smooth driving, idling, and speed.</li>
          <li style={li}><b>Trip summary statistics</b> — total distance, trip duration, idle time, number of harsh events, and maximum speed.</li>
        </ul>
        <p style={{ ...p, background: '#E8F8F5', padding: '12px 16px', borderRadius: 8, borderLeft: `4px solid ${C.teal}` }}><b>On-device processing:</b> Raw GPS coordinates and sensor data are processed on your device during the trip. Once the trip ends and your driving score is calculated, the raw GPS track and raw sensor readings are <b>discarded and not transmitted to our servers</b>. Only the aggregated trip metrics and driving score are stored.</p>
        <p style={p}><b>Driving style profile:</b> Over time, the App builds a driving style profile based on your historical trip summaries. This profile is used solely to provide you with personalised coaching tips, track your improvement over time, and award rewards points. It is never shared with insurers, employers, or third parties for any purpose other than operating the rewards programme.</p>

        <h3 style={subsec}>4.3 Wellness and Health Data</h3>
        <p style={p}><b>4.3.1 Fingertip Pulse Scan</b></p>
        <p style={p}>The App uses your device&rsquo;s rear camera and flash to perform a non-invasive fingertip pulse scan lasting approximately 30 seconds. During the scan:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}>Camera images are processed <b>entirely on your device</b> using photoplethysmography (PPG) algorithms.</li>
          <li style={li}>No camera images, video frames, or raw optical data are uploaded to our servers or stored anywhere.</li>
          <li style={li}>Only the derived health metrics are saved: resting heart rate (BPM), heart-rate variability (HRV), breathing rate, an estimated stress level, and an overall wellness score. The beat-to-beat timing intervals detected during the scan are sent to our servers solely to compute these metrics and are not stored. The App does <b>not</b> measure blood pressure or blood oxygen (SpO2).</li>
        </ul>
        <p style={p}><b>4.3.2 Step and Workout Sync (Health Connect / Apple Health)</b></p>
        <p style={p}><b>Step and workout sync is an optional feature and may not be available in your version of the App.</b> Where it is offered, it is <b>off until you switch it on</b> and grant permission. If it is not available in your version, or you never switch it on, the App reads no data from Google Health Connect or Apple Health at all.</p>
        <p style={p}>If you do switch it on and grant permission, the App reads two things only: (1) <b>your daily step count total</b> &mdash; on Android from Google Health Connect (the <code>READ_STEPS</code> permission), and on iOS from Apple Health; and (2) <b>your exercise sessions</b> &mdash; on Android from Google Health Connect (the <code>READ_EXERCISE</code> permission): the type of exercise, its start and end time and its duration. Both are used solely to award <b>wellness rewards points</b> for physical activity &mdash; <b>never for insurance pricing</b>. We read the current and previous two calendar days only. We do <b>not</b> read exercise routes or GPS traces, heart rate, sleep, nutrition, body measurements, reproductive health, or any other category. The App <b>never writes</b> any data to Health Connect or Apple Health. Reading happens only when you tap &ldquo;Sync steps and workouts&rdquo; in the App &mdash; there is no background reading. You can withdraw the permission at any time in your phone&rsquo;s settings, which stops the reading immediately.</p>

        <h3 style={subsec}>4.4 Food and Nutrition Review Data</h3>
        <p style={p}>The App includes a meal-logging feature that allows you to photograph meals and earn rewards points for healthy eating. When you submit a meal photo:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}>The image is transmitted to an automated food-recognition service (powered by machine learning) solely to verify that the image contains real food and to classify the meal type and general nutritional category.</li>
          <li style={li}>The food-recognition service processes the image in real-time and returns a classification result. <b>We do not store your meal photo.</b> It is processed by the service solely to return the classification and is not used to train its models.</li>
          <li style={li}>We store only: the date and time of submission, the meal classification result, and the points awarded.</li>
          <li style={li}>Meal photos are <b>never</b> used to identify you, other individuals in the image, your location, or any information beyond the food content itself.</li>
          <li style={li}>No one at Alpha Direct views your meal photos unless you submit a dispute about points awarded, in which case a designated team member may review the specific image you submitted.</li>
        </ul>

        <h3 style={subsec}>4.5 Activity and Rewards Data</h3>
        <p style={p}>We collect and store information related to your participation in the rewards programme: points earned, redeemed, and current balance; tier status and progression history; meal photos you submit for verification (scored, not stored), daily step totals and exercise sessions (type, start/end time, duration) you sync from your phone; challenges you join and your completion status; leaderboard rankings and achievement badges.</p>

        <h3 style={subsec}>4.6 Technical and Device Information</h3>
        <p style={p}>To ensure the App functions correctly and to diagnose technical issues, we collect: device type, operating system version, and App version; anonymised crash reports and performance metrics; push notification tokens (if you enable notifications); and IP address (used for security purposes and approximate geographic region only — not stored long-term).</p>
        <p style={p}>We do <b>not</b> collect your device&rsquo;s unique hardware identifiers (IMEI, MAC address) or access your contacts, call logs, SMS messages, or files stored on your device.</p>

        {/* 5. How We Use */}
        <h2 style={sec}>5. How We Use Your Information</h2>
        <p style={p}>We use the personal information collected through the App for the following purposes only:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}><b>Operating the rewards programme</b> — creating and maintaining your account, calculating points and scores, determining tier status, processing reward redemptions, and displaying your standing on leaderboards.</li>
          <li style={li}><b>Providing personalised insights</b> — delivering driving coaching tips, wellness trend reports, activity summaries, and nutritional guidance based on your data.</li>
          <li style={li}><b>Improving the App</b> — analysing aggregated, de-identified usage patterns to improve features, fix bugs, and develop new functionality.</li>
          <li style={li}><b>Communicating with you</b> — sending transactional messages (sign-in codes, points notifications, reward confirmations) and, with your separate consent, promotional messages about new features or challenges.</li>
          <li style={li}><b>Ensuring security and preventing fraud</b> — detecting unusual account activity, preventing abuse of the rewards system, and protecting the integrity of leaderboards.</li>
          <li style={li}><b>Complying with legal obligations</b> — responding to lawful requests from regulatory authorities and maintaining records as required by law.</li>
        </ul>

        {/* 6. What We Do NOT Do */}
        <h2 style={sec}>6. What We Do NOT Do With Your Data</h2>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}><b>No insurance use.</b> Your wellness scores, driving data, meal submissions, step counts, or any other App data are never used for insurance underwriting, premium calculation, claims decisions, policy cancellations, or any actuarial purpose.</li>
          <li style={li}><b>No sale of data.</b> We do not sell, rent, lease, or trade your personal information to any third party for their marketing or commercial purposes.</li>
          <li style={li}><b>No profiling for automated decisions with legal effect.</b> We do not use your data to make automated decisions that produce legal effects or similarly significant effects on you, as contemplated by Section 43 of the Botswana DPA and Section 71 of POPIA.</li>
          <li style={li}><b>No surveillance.</b> The App does not continuously track your location. GPS is active only during an active Click&nbsp;&amp;&nbsp;Drive recording session that you manually start and stop.</li>
          <li style={li}><b>No biometric identification.</b> Camera data from the wellness scan is not used for facial recognition or any biometric identification purpose.</li>
          <li style={li}><b>No audio recording.</b> Although the microphone permission may be requested by the device during the wellness scan, no audio is recorded, processed, or stored.</li>
        </ul>

        {/* 7. Data Sharing */}
        <h2 style={sec}>7. Data Sharing and Third-Party Processors</h2>
        <p style={p}>We share your personal information only in the following limited circumstances:</p>
        <div style={{ overflowX: 'auto' }}>
          <table style={table}>
            <thead><tr><th style={th}>Recipient</th><th style={th}>Purpose</th><th style={th}>Safeguards</th></tr></thead>
            <tbody>
              <tr><td style={td}>Cloud hosting provider</td><td style={td}>Secure storage of account and rewards data</td><td style={td}>Data processing agreement, encryption at rest and in transit, ISO 27001 certified</td></tr>
              <tr><td style={td}>Food-recognition AI service</td><td style={td}>Classifying meal photos for points</td><td style={td}>Image sent only to return a classification; not stored by us</td></tr>
              <tr><td style={td}>Push notification service</td><td style={td}>Delivering notifications to your device</td><td style={td}>Only notification tokens shared; no personal content</td></tr>
                            <tr><td style={td}>Law enforcement or regulators</td><td style={td}>Where required by law or valid court order</td><td style={td}>Only disclosed to the minimum extent legally required</td></tr>
            </tbody>
          </table>
        </div>
        <p style={p}>All third-party processors are bound by written data processing agreements that require them to process your data only on our instructions, maintain appropriate security measures, and delete or return data upon termination of the agreement, in accordance with Section 30 of the Botswana DPA and Section 21 of POPIA.</p>

        {/* 8. Cross-Border */}
        <h2 style={sec}>8. Cross-Border Data Transfers</h2>
        <p style={p}>Your personal information may be transferred to and processed in countries outside of Botswana and South Africa where our service providers operate. In such cases, we ensure that:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}>The receiving country has been determined to provide an adequate level of data protection (as per the Botswana Commission&rsquo;s list of adequate jurisdictions), <b>or</b></li>
          <li style={li}>Appropriate safeguards are in place, including binding contractual clauses that guarantee the same level of protection as afforded under the DPA and POPIA, <b>or</b></li>
          <li style={li}>You have provided explicit, informed consent to the transfer after being made aware of the potential risks.</li>
        </ul>
        <p style={p}>South Africa is recognised as an adequate jurisdiction by the Botswana Commission. Data transfers between our Botswana and South African operations are therefore permitted without additional safeguards.</p>

        {/* 9. Data Security */}
        <h2 style={sec}>9. Data Security</h2>
        <p style={p}>We implement appropriate technical and organisational measures to protect your personal information against unauthorised access, unlawful processing, accidental loss, destruction, or damage, as required by Section 62 of the Botswana DPA and Condition 7 of POPIA. These measures include:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}><b>Encryption:</b> All data is encrypted in transit using TLS 1.3 and at rest using AES-256 encryption.</li>
          <li style={li}><b>Access control:</b> Strict role-based access controls ensure that only authorised personnel can access personal data, on a need-to-know basis.</li>
          <li style={li}><b>Authentication:</b> Passwordless authentication via one-time codes reduces the risk of credential theft.</li>
          <li style={li}><b>Infrastructure security:</b> Our hosting infrastructure is protected by firewalls, intrusion detection systems, and regular vulnerability assessments.</li>
          <li style={li}><b>Employee training:</b> All staff with access to personal data receive regular data protection training.</li>
          <li style={li}><b>Incident response:</b> We maintain a documented data breach response plan and will notify the relevant supervisory authority within 72 hours of becoming aware of a qualifying breach, and affected individuals without undue delay where the breach poses a high risk to their rights and freedoms.</li>
        </ul>

        {/* 10. Retention */}
        <h2 style={sec}>10. Data Retention</h2>
        <p style={p}>We retain your personal information only for as long as necessary to fulfil the purposes for which it was collected, in accordance with the storage limitation principle (Section 22 of the Botswana DPA):</p>
        <div style={{ overflowX: 'auto' }}>
          <table style={table}>
            <thead><tr><th style={th}>Data Category</th><th style={th}>Retention Period</th></tr></thead>
            <tbody>
              <tr><td style={td}>Account information</td><td style={td}>Duration of active account + 12 months after deletion request</td></tr>
              <tr><td style={td}>Driving trip summaries and scores</td><td style={td}>Duration of active account; deleted within 30 days of account closure</td></tr>
              <tr><td style={td}>Driving style profile</td><td style={td}>Duration of active account; deleted within 30 days of account closure</td></tr>
              <tr><td style={td}>Wellness scan results</td><td style={td}>Duration of active account; deleted within 30 days of account closure</td></tr>
              <tr><td style={td}>Meal classification results</td><td style={td}>Duration of active account; deleted within 30 days of account closure</td></tr>
              <tr><td style={td}>Step count and exercise session data</td><td style={td}>Duration of active account; deleted when you delete your account</td></tr>
              <tr><td style={td}>Points and rewards history</td><td style={td}>Duration of active account + 24 months (for audit purposes)</td></tr>
              <tr><td style={td}>Technical/diagnostic logs</td><td style={td}>Maximum 90 days, then automatically deleted</td></tr>
              <tr><td style={td}>Raw GPS tracks</td><td style={td}>Not retained — discarded on-device after trip score calculation</td></tr>
              <tr><td style={td}>Meal photos</td><td style={td}>Not retained — discarded immediately after classification</td></tr>
              <tr><td style={td}>Camera/pulse scan images</td><td style={td}>Not retained — never leave your device</td></tr>
            </tbody>
          </table>
        </div>
        <p style={p}>Upon account deletion, we will erase or de-identify all personal information within the timeframes specified above, unless retention is required by law.</p>

        {/* 11. Your Rights */}
        <h2 style={sec}>11. Your Rights as a Data Subject</h2>
        <p style={p}>Under the Botswana Data Protection Act (Part VIII) and POPIA (Chapter 2, Section 5), you have the following rights:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}><b>Right to Be Informed</b> — You have the right to know what personal information we collect, why we collect it, how we use it, and with whom we share it.</li>
          <li style={li}><b>Right of Access</b> — You may request a copy of all personal information we hold about you. We will respond within 30 days.</li>
          <li style={li}><b>Right to Rectification</b> — If any personal information we hold about you is inaccurate, incomplete, or outdated, you have the right to request correction.</li>
          <li style={li}><b>Right to Erasure</b> — You may request the deletion of your personal information. We will erase your data in accordance with the retention schedule in Section 10.</li>
          <li style={li}><b>Right to Object</b> — You may object to the processing of your personal information on grounds relating to your particular situation, and to processing for direct marketing at any time.</li>
          <li style={li}><b>Right to Data Portability</b> — You may request your personal information in a structured, commonly used, and machine-readable format.</li>
          <li style={li}><b>Right to Withdraw Consent</b> — Where processing is based on your consent, you may withdraw that consent at any time without affecting the lawfulness of prior processing.</li>
          <li style={li}><b>Right Not to Be Subject to Automated Decision-Making</b> — You have the right not to be subject to decisions based solely on automated processing that produce legal effects concerning you.</li>
          <li style={li}><b>Right to Lodge a Complaint</b> — You may lodge a complaint with the Information and Data Protection Commission (Botswana) or the Information Regulator (South Africa).</li>
        </ul>

        {/* 12. How to Exercise Rights */}
        <h2 style={sec}>12. How to Exercise Your Rights</h2>
        <p style={p}>To exercise any of the rights described above, contact us at <a href="mailto:privacy@alphadirect.co.bw" style={{ color: C.teal }}>privacy@alphadirect.co.bw</a> with the subject line &ldquo;Data Subject Request&rdquo;. We will verify your identity before processing any request and respond within 30 calendar days. If a request is particularly complex, we may extend this period by a further 30 days with notice. There is no fee for exercising your rights unless a request is manifestly unfounded or excessive.</p>

        {/* 13. Device Permissions */}
        <h2 style={sec}>13. Device Permissions Explained</h2>
        <div style={{ overflowX: 'auto' }}>
          <table style={table}>
            <thead><tr><th style={th}>Permission</th><th style={th}>Purpose</th><th style={th}>When Active</th></tr></thead>
            <tbody>
              <tr><td style={td}>Camera</td><td style={td}>Fingertip pulse scan (wellness measurement)</td><td style={td}>Only during an active scan session</td></tr>
              <tr><td style={td}>Location (GPS)</td><td style={td}>Calculating driving metrics during Click &amp; Drive</td><td style={td}>Only while actively recording a trip</td></tr>
                            <tr><td style={td}>Health Connect / Apple Health <i>(step and workout sync, where available)</i></td><td style={td}>Reading your daily step count total and your exercise sessions (type, start/end time, duration) for wellness rewards points &mdash; never insurance pricing</td><td style={td}>Only if you switch sync on and grant permission; read only when you tap sync; revocable at any time</td></tr>
              <tr><td style={td}>Internet</td><td style={td}>Transmitting scores, syncing rewards, receiving notifications</td><td style={td}>Continuous while App is in use</td></tr>
              <tr><td style={td}>Notifications</td><td style={td}>Sending points updates, challenge reminders, and reward alerts</td><td style={td}>Only if you enable push notifications</td></tr>
            </tbody>
          </table>
        </div>
        <p style={p}>You may revoke any permission at any time through your device&rsquo;s settings. Revoking a permission will disable the corresponding feature but will not affect the rest of the App&rsquo;s functionality.</p>

        {/* 14. Children */}
        <h2 style={sec}>14. Children&rsquo;s Privacy</h2>
        <p style={p}>Alpha&nbsp;Nexus is not intended for use by anyone under the age of 18. We do not knowingly collect personal information from children. If we become aware that we have inadvertently collected personal information from a child under 18, we will take immediate steps to delete that information. In accordance with Section 35 of the Botswana DPA and Section 34 of POPIA, processing of children&rsquo;s personal information requires the consent of a competent person (parent or guardian).</p>

        {/* 15. Privacy by Design */}
        <h2 style={sec}>15. Data Protection by Design and Default</h2>
        <p style={p}>In compliance with Section 52 of the Botswana DPA, we have implemented data protection by design and by default throughout the App:</p>
        <ul style={{ paddingLeft: 20, margin: '0 0 14px' }}>
          <li style={li}><b>Minimisation by design:</b> We collect only the minimum data necessary for each feature. Raw sensor data and images are processed on-device wherever possible.</li>
          <li style={li}><b>Privacy by default:</b> All data-sharing features (step and workout sync, Click &amp; Drive, meal logging) are opt-in and off until you switch them on. No data is collected until you actively enable them.</li>
          <li style={li}><b>Pseudonymisation:</b> Where feasible, we use pseudonymised identifiers rather than directly identifying information in our analytics.</li>
          <li style={li}><b>Separation of concerns:</b> Rewards data, wellness data, and driving data are stored in logically separated databases and are not cross-referenced with any insurance policy data held by Alpha Direct.</li>
        </ul>

        {/* 16. DPIA */}
        <h2 style={sec}>16. Data Protection Impact Assessment</h2>
        <p style={p}>Given that the App processes health-related data and location data, we have conducted a Data Protection Impact Assessment (DPIA) as required by Section 55 of the Botswana DPA for high-risk processing activities. The DPIA concluded that the safeguards described in this Policy adequately mitigate the risks to data subjects&rsquo; rights and freedoms. A summary of the DPIA is available upon request to our Information Officer.</p>

        {/* 17. Cookies */}
        <h2 style={sec}>17. Cookies and Tracking Technologies</h2>
        <p style={p}>The Alpha&nbsp;Nexus mobile application does not use browser cookies. We do not employ cross-app tracking, advertising identifiers, or third-party tracking pixels. We do not participate in any advertising networks or share data with advertisers.</p>

        {/* 18. Changes */}
        <h2 style={sec}>18. Changes to This Privacy Policy</h2>
        <p style={p}>We may update this Privacy Policy from time to time to reflect changes in our practices, the App&rsquo;s features, or applicable law. When we make material changes, we will update the &ldquo;Last Updated&rdquo; date at the top of this Policy and notify you via an in-app notification or email before the changes take effect. Where required by law, we will seek your renewed consent for any new processing activities.</p>

        {/* 19. Governing Law */}
        <h2 style={sec}>19. Governing Law and Jurisdiction</h2>
        <p style={p}>This Privacy Policy is governed by the laws of the Republic of Botswana. Any disputes arising from or in connection with this Policy shall be subject to the exclusive jurisdiction of the courts of Botswana, without prejudice to your right to lodge a complaint with the relevant data protection authority in your country of residence. For users in South Africa, this Policy is additionally subject to the provisions of POPIA.</p>

        {/* 20. Contact */}
        <h2 style={sec}>20. Contact Us</h2>
        <p style={p}>If you have any questions, concerns, or requests regarding this Privacy Policy or our data protection practices, please contact us:</p>
        <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 12, padding: '16px 20px', margin: '0 0 14px' }}>
          <p style={{ ...p, margin: '0 0 4px' }}><b>Alpha Direct Insurance Company (Pty) Ltd</b></p>
          <p style={{ ...p, margin: '0 0 4px', fontSize: 14 }}>Plot 54373, Western Commercial Road, New CBD, Gaborone, Botswana</p>
          <p style={{ ...p, margin: '0 0 4px', fontSize: 14 }}><b>Information Officer:</b> <a href="mailto:privacy@alphadirect.co.bw" style={{ color: C.teal }}>privacy@alphadirect.co.bw</a></p>
          <p style={{ ...p, margin: '0 0 4px', fontSize: 14 }}><b>General Enquiries:</b> <a href="mailto:cfo@alphadirect.co.bw" style={{ color: C.teal }}>cfo@alphadirect.co.bw</a></p>
          <p style={{ ...p, margin: 0, fontSize: 14 }}><b>Telephone:</b> +267 393 2377</p>
        </div>

        <p style={{ ...p, color: C.inkSoft, fontSize: 13, marginTop: 40, textAlign: 'center' as const, fontStyle: 'italic' }}>This Privacy Policy was last reviewed and approved by the Alpha Direct Insurance Company Board of Directors on 30 July 2026.</p>
      </main>
    </div>
  )
}
