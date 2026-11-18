using System;
using System.Threading.Tasks;
using Microsoft.Playwright;

namespace SmartCommerce.E2E.Pages
{
    public class PaymentGatewayPage
    {
        private readonly IPage _page;

        // Frame & Locators
        private IFrameLocator StripePaymentFrame => _page.FrameLocator("iframe[name^='__privateStripeFrame']");
        private IFrameLocator ThreeDSecureModalFrame => _page.FrameLocator("iframe#threeds-challenge-iframe");
        private ILocator CardNumberInput => StripePaymentFrame.Locator("input[name='cardnumber']");
        private ILocator CardExpiryInput => StripePaymentFrame.Locator("input[name='exp-date']");
        private ILocator CardCvcInput => StripePaymentFrame.Locator("input[name='cvc']");
        private ILocator SubmitOrderButton => _page.GetByTestId("place-order-submit-btn");
        private ILocator ThreeDSecureApproveBtn => ThreeDSecureModalFrame.GetByRole(AriaRole.Button, new() { Name = "Complete Authentication" });
        private ILocator OrderConfirmationHeader => _page.GetByTestId("order-confirmation-heading");
        private ILocator PaymentDeclinedBanner => _page.GetByTestId("payment-error-toast");

        public PaymentGatewayPage(IPage page)
        {
            _page = page;
        }

        public async Task EnterCreditCardDetailsAsync(string cardNumber, string expiry, string cvc)
        {
            await CardNumberInput.WaitForAsync(new LocatorWaitForOptions
            {
                State = WaitForSelectorState.Visible,
                Timeout = 15000
            });
            await CardNumberInput.FillAsync(cardNumber);
            await CardExpiryInput.FillAsync(expiry);
            await CardCvcInput.FillAsync(cvc);
        }

        public async Task SubmitPaymentAndHandle3DSAsync(bool expect3DSChallenge = true)
        {
            await SubmitOrderButton.ClickAsync();

            if (expect3DSChallenge)
            {
                await ThreeDSecureApproveBtn.WaitForAsync(new LocatorWaitForOptions
                {
                    State = WaitForSelectorState.Visible,
                    Timeout = 20000
                });
                await ThreeDSecureApproveBtn.ClickAsync();
            }

            await _page.WaitForURLAsync("**/checkout/confirmation**", new PageWaitForURLOptions
            {
                WaitUntil = WaitUntilState.NetworkIdle,
                Timeout = 30000
            });
        }

        public async Task SubmitInstantPaymentAsync()
        {
            var chargeResponseTask = _page.WaitForResponseAsync(
                resp => resp.Url.Contains("/api/v2/payments/charge"),
                new PageWaitForResponseOptions { Timeout = 25000 });

            await SubmitOrderButton.ClickAsync();
            var response = await chargeResponseTask;

            if (response.Status >= 500)
            {
                throw new PlaywrightException($"Payment Gateway Charge endpoint returned HTTP {response.Status} ({response.StatusText})");
            }
        }

        public async Task AssertOrderConfirmedAsync()
        {
            await Assertions.Expect(OrderConfirmationHeader).ToContainTextAsync("Thank you for your order", new LocatorAssertionsToContainTextOptions
            {
                Timeout = 10000
            });
        }
    }
}
