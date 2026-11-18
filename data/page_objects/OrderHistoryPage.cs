using System;
using System.Threading.Tasks;
using Microsoft.Playwright;

namespace SmartCommerce.E2E.Pages
{
    public class OrderHistoryPage
    {
        private readonly IPage _page;

        // Locators
        private ILocator OrderSearchInput => _page.GetByPlaceholder("Search by Order ID or SKU");
        private ILocator DateRangeFilterDropdown => _page.GetByTestId("orders-timeframe-select");
        private ILocator OrderCardRows => _page.Locator("article[data-testid='order-history-card']");
        private ILocator DownloadInvoicePdfButton => _page.Locator("a[data-testid='download-tax-invoice-btn']");
        private ILocator InitiateReturnButton => _page.GetByRole(AriaRole.Button, new() { Name = "Return or Replace Items" });
        private ILocator ReturnReasonRadioOptions => _page.Locator("input[name='rmaReasonCode']");
        private ILocator SubmitRmaRequestButton => _page.GetByTestId("submit-rma-confirm-btn");
        private ILocator LiveCarrierTrackingBadge => _page.GetByTestId("carrier-webhook-status-pill");
        private ILocator PaginationNextButton => _page.GetByRole(AriaRole.Button, new() { Name = "Next page" });

        public OrderHistoryPage(IPage page)
        {
            _page = page;
        }

        public async Task FilterOrdersByTimeframeAsync(string timeframeOptionValue)
        {
            var ordersApiTask = _page.WaitForResponseAsync(
                resp => resp.Url.Contains("/api/v2/orders/history"),
                new PageWaitForResponseOptions { Timeout = 15000 });

            await DateRangeFilterDropdown.SelectOptionAsync(new SelectOptionValue { Value = timeframeOptionValue });
            var response = await ordersApiTask;
            if (response.Status >= 500)
            {
                throw new PlaywrightException($"Orders History GraphQL/REST endpoint failed with HTTP {response.Status}");
            }
        }

        public async Task SearchOrderByIdAsync(string orderId)
        {
            await OrderSearchInput.FillAsync(orderId);
            await OrderSearchInput.PressAsync("Enter");
            await Assertions.Expect(OrderCardRows.First).ToContainTextAsync(orderId, new LocatorAssertionsToContainTextOptions
            {
                Timeout = 10000
            });
        }

        public async Task<IDownload> DownloadTaxInvoicePdfAsync(string orderId)
        {
            var orderCard = OrderCardRows.Filter(new() { HasText = orderId });
            var downloadTask = _page.WaitForDownloadAsync(new PageWaitForDownloadOptions { Timeout = 20000 });
            await orderCard.Locator("a[data-testid='download-tax-invoice-btn']").ClickAsync();
            return await downloadTask;
        }

        public async Task SubmitReturnMerchandiseAuthorizationAsync(string orderId, string reasonValue)
        {
            var orderCard = OrderCardRows.Filter(new() { HasText = orderId });
            await orderCard.GetByRole(AriaRole.Button, new() { Name = "Return or Replace Items" }).ClickAsync();
            await ReturnReasonRadioOptions.Filter(new() { HasText = reasonValue }).CheckAsync();
            await SubmitRmaRequestButton.ClickAsync();
            await Assertions.Expect(_page.GetByTestId("rma-confirmation-banner")).ToBeVisibleAsync();
        }

        public async Task AssertCarrierTrackingStatusAsync(string expectedStatusText)
        {
            await Assertions.Expect(LiveCarrierTrackingBadge).ToHaveTextAsync(expectedStatusText, new LocatorAssertionsToHaveTextOptions
            {
                Timeout = 12000
            });
        }
    }
}
