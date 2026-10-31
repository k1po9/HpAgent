import { expect, type Locator, type Page } from "@playwright/test";

/** Choose a visible option from a Lobe select, including its portalled listbox. */
export async function selectTask(page: Page, owner: Locator | Page, field: string, option: string) {
  const select = owner.getByLabel(field, { exact: true });
  await expect(select).toHaveAttribute("role", "combobox");
  await select.click();
  await page.getByRole("option", { name: option, exact: true }).click();
  await expect(select).toHaveText(option);
  await expect(select).toHaveAttribute("aria-expanded", "false");
}
