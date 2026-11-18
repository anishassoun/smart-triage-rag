using System;
using System.Threading.Tasks;
using Microsoft.Playwright;

namespace SmartCommerce.E2E.Pages
{
    public class ShoppingCartPage
    {
        private readonly IPage _page;

        // Locators
        private ILocator CartDrawerContainer => _page.GetByTestId("slideout-cart-drawer");
        private ILocator CartLineItemRows => _page.Locator("div[data-testid='cart-line-item-row']");
        private ILocator QuantityStepperPlusBtn => _page.Locator("button.qty-stepper-increment");
        private ILocator RemoveItemIconBtn => _page.GetByRole(AriaRole.Button, new() { Name = "Remove item" });
        private ILocator RemoveConfirmModalBtn => _page.GetByTestId("confirm-remove-item-modal-btn");
        private ILocator CartEmptyStateBanner => _page.GetByTestId("empty-cart-illustration-banner");
        private ILocator CartSubtotalValue => _page.GetByTestId("cart-drawer-subtotal-value");
        private ILocator FreeShippingProgressBar => _page.Locator("div[role='progressbar'][data-testid='free-shipping-meter']");
        private ILocator ProceedToCheckoutBtn => _page.GetByRole(AriaRole.Button, new() { Name = "Checkout" });
        private ILocator UpsellCarouselAddBtn => _page.Locator("section.cart-upsell-widget button[data-action='quick-add']");

        public ShoppingCartPage(IPage page)
        {
            _page = page;
        }

        public async Task OpenCartDrawerAsync()
        {
            await _page.GetByTestId("navbar-cart-trigger").ClickAsync();
            await Assertions.Expect(CartDrawerContainer).ToBeVisibleAsync(new LocatorAssertionsToBeVisibleOptions
            {
                Timeout = 10000
            });
        }

        public async Task IncrementItemQuantityAsync(string skuCode, int clicks = 1)
        {
            var itemRow = CartLineItemRows.Filter(new() { HasText = skuCode });
            var plusButton = itemRow.Locator("button.qty-stepper-increment");

            for (int i = 0; i < clicks; i++)
            {
                var recalcTask = _page.WaitForResponseAsync(
                    resp => resp.Url.Contains("/api/v2/cart/items") && resp.Status == 200,
                    new PageWaitForResponseOptions { Timeout = 12000 });
                await plusButton.ClickAsync();
                await recalcTask;
            }
        }

        public async Task RemoveLineItemAsync(string productName)
        {
            var itemRow = CartLineItemRows.Filter(new() { HasText = productName });
            await itemRow.GetByRole(AriaRole.Button, new() { Name = "Remove item" }).ClickAsync();
            await Assertions.Expect(itemRow).ToBeHiddenAsync(new LocatorAssertionsToBeHiddenOptions
            {
                Timeout = 8000
            });
        }

        public async Task AddRecommendedUpsellItemAsync(int index = 0)
        {
            var quickAddBtn = UpsellCarouselAddBtn.Nth(index);
            await quickAddBtn.ScrollIntoViewIfNeededAsync();
            await quickAddBtn.ClickAsync(new LocatorClickOptions { Timeout = 10000 });
            await _page.WaitForLoadStateAsync(LoadState.NetworkIdle);
        }

        public async Task NavigateToCheckoutFromDrawerAsync()
        {
            await ProceedToCheckoutBtn.ClickAsync();
            await _page.WaitForURLAsync("**/checkout/shipping**", new PageWaitForURLOptions
            {
                Timeout = 15000
            });
        }

        public async Task AssertFreeShippingUnlockedAsync()
        {
            await Assertions.Expect(FreeShippingProgressBar).ToHaveAttributeAsync("aria-valuenow", "100");
        }
    }
}
