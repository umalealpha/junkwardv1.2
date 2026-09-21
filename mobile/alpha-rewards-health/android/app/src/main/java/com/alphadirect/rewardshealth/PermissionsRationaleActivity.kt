package com.alphadirect.rewardshealth

import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

/**
 * Shown when the user taps the privacy-policy / rationale link inside the
 * Health Connect permission UI. Health Connect requires this activity to exist
 * and respond to androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE.
 *
 * Google Play requires this page to actually load: the previous address
 * (alphadirect.co.bw/privacy/health-rewards) returned 404, which is a rejection
 * risk for a health app. If this URL ever moves, the SAME address must also be
 * set in the Play Console listing's privacy-policy field.
 */
class PermissionsRationaleActivity : AppCompatActivity() {

  companion object {
    /** Verified live (HTTP 200) on 2026-07-25. */
    const val PRIVACY_POLICY_URL = "https://omni.alphadirect.co.bw/m/privacy"
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)

    val webView = WebView(this)
    webView.webViewClient = object : WebViewClient() {
      override fun shouldOverrideUrlLoading(
        view: WebView?,
        request: WebResourceRequest?,
      ): Boolean = false
    }

    webView.loadUrl(PRIVACY_POLICY_URL)

    setContentView(webView)
  }
}
