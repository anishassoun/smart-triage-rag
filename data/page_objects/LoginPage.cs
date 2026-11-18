using System;
using System.Threading.Tasks;
using Microsoft.Playwright;

namespace SmartCommerce.E2E.Pages
{
    public class LoginPage
    {
        private readonly IPage _page;

        // Locators
        private ILocator EmailInput => _page.GetByTestId("auth-email-input");
        private ILocator PasswordInput => _page.GetByTestId("auth-password-input");
        private ILocator SignInButton => _page.Locator("button[data-testid='login-submit-btn']");
        private ILocator SsoEnterpriseButton => _page.GetByRole(AriaRole.Button, new() { Name = "Continue with Corporate SSO" });
        private ILocator MfaOtpDigits => _page.Locator("input.mfa-otp-digit-input");
        private ILocator VerifyMfaButton => _page.GetByTestId("mfa-verify-submit");
        private ILocator ErrorAlertBanner => _page.GetByRole(AriaRole.Alert).Filter(new() { HasTextRegex = new("Invalid|Locked|Expired") });
        private ILocator UserProfileAvatar => _page.GetByTestId("navbar-user-avatar");
        private ILocator CookieConsentAcceptBtn => _page.Locator("#onetrust-accept-btn-handler");

        public LoginPage(IPage page)
        {
            _page = page;
        }

        public async Task NavigateAsync(string baseUrl)
        {
            await _page.GotoAsync($"{baseUrl}/auth/login", new PageGotoOptions
            {
                WaitUntil = WaitUntilState.DOMContentLoaded,
                Timeout = 30000
            });
        }

        public async Task DismissCookieBannerIfPresentAsync()
        {
            if (await CookieConsentAcceptBtn.IsVisibleAsync())
            {
                await CookieConsentAcceptBtn.ClickAsync();
            }
        }

        public async Task LoginWithCredentialsAsync(string email, string password)
        {
            await EmailInput.FillAsync(email);
            await PasswordInput.FillAsync(password);
            await SignInButton.ClickAsync(new LocatorClickOptions { Timeout = 10000 });
        }

        public async Task CompleteMfaChallengeAsync(string sixDigitCode)
        {
            await MfaOtpDigits.First.WaitForAsync(new LocatorWaitForOptions { State = WaitForSelectorState.Visible });
            await MfaOtpDigits.FillAsync(sixDigitCode);
            await VerifyMfaButton.ClickAsync();
        }

        public async Task InitiateSsoLoginAsync(string idpDomain)
        {
            await EmailInput.FillAsync($"test.user@{idpDomain}");
            var popupTask = _page.WaitForPopupAsync();
            await SsoEnterpriseButton.ClickAsync();
            var ssoPopup = await popupTask;
            await ssoPopup.WaitForLoadStateAsync(LoadState.NetworkIdle);
        }

        public async Task AssertAuthenticatedSessionAsync()
        {
            await Assertions.Expect(UserProfileAvatar).ToBeVisibleAsync(new LocatorAssertionsToBeVisibleOptions
            {
                Timeout = 15000
            });
        }

        public async Task<string> GetAuthErrorMessageAsync()
        {
            await ErrorAlertBanner.WaitForAsync(new LocatorWaitForOptions { State = WaitForSelectorState.Visible, Timeout = 5000 });
            return await ErrorAlertBanner.InnerTextAsync();
        }
    }
}
