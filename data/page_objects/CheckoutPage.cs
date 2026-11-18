using System;
using System.Threading.Tasks;
using Microsoft.Playwright;

namespace SmartCommerce.E2E.Pages
{
    public class CheckoutPage
    {
        private readonly IPage _page;

        // Locators
        private ILocator ShippingFirstNameInput => _page.GetByTestId("shipping-first-name");
        private ILocator ShippingLastNameInput => _page.GetByTestId("shipping-last-name");
        private ILocator AddressAutocompleteInput => _page.Locator("input[name='shippingAddressLine1']");
        private ILocator AddressSuggestionItems => _page.Locator("ul.pac-container li.pac-item");
        private ILocator PostalCodeInput => _page.GetByTestId("shipping-postal-code");
        private ILocator ShippingMethodRadioGroup => _page.Locator("div[data-testid='shipping-tier-option']");
        private ILocator PromoCodeInput => _page.GetByPlaceholder("Enter promo or gift code");
        private ILocator ApplyPromoButton => _page.GetByRole(AriaRole.Button, new() { Name = "Apply" });
        private ILocator PromoDiscountRow => _page.GetByTestId("order-summary-discount-amount");
        private ILocator OrderTotalAmount => _page.GetByTestId("checkout-grand-total-value");
        private ILocator ProceedToPaymentButton => _page.Locator("#btn-proceed-to-payment");
        private ILocator InventoryReservationSpinner => _page.GetByTestId("inventory-lock-spinner");

        public CheckoutPage(IPage page)
        {
            _page = page;
        }

        public async Task FillShippingAddressAsync(string firstName, string lastName, string streetQuery, string postalCode)
        {
            await ShippingFirstNameInput.FillAsync(firstName);
            await ShippingLastNameInput.FillAsync(lastName);
            await AddressAutocompleteInput.FillAsync(streetQuery);
            await AddressSuggestionItems.First.ClickAsync(new LocatorClickOptions { Timeout = 8000 });
            await PostalCodeInput.FillAsync(postalCode);
        }

        public async Task SelectShippingTierAsync(string tierCode)
        {
            var tierOption = ShippingMethodRadioGroup.Filter(new() { HasText = tierCode });
            await tierOption.ClickAsync();
            await _page.WaitForResponseAsync(resp =>
                resp.Url.Contains("/api/v2/checkout/shipping-rates") && resp.Status == 200);
        }

        public async Task ApplyPromoCodeAsync(string promoCode)
        {
            await PromoCodeInput.FillAsync(promoCode);
            await ApplyPromoButton.ClickAsync();
            await Assertions.Expect(PromoDiscountRow).ToBeVisibleAsync(new LocatorAssertionsToBeVisibleOptions
            {
                Timeout = 10000
            });
        }

        public async Task ProceedToPaymentStepAsync()
        {
            await Assertions.Expect(InventoryReservationSpinner).ToBeHiddenAsync();
            var reservationResponseTask = _page.WaitForResponseAsync(
                resp => resp.Url.Contains("/api/v2/inventory/reserve"),
                new PageWaitForResponseOptions { Timeout = 15000 });

            await ProceedToPaymentButton.ClickAsync();
            var response = await reservationResponseTask;
            if (!response.Ok)
            {
                throw new PlaywrightException($"Inventory reservation API failed with HTTP {response.Status}: {response.StatusText}");
            }
        }

        public async Task<string> GetGrandTotalTextAsync()
        {
            await OrderTotalAmount.WaitForAsync(new LocatorWaitForOptions { State = WaitForSelectorState.Visible });
            return await OrderTotalAmount.InnerTextAsync();
        }
    }
}
