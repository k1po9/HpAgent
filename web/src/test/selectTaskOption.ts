import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/** Exercise the real Lobe listbox rather than dispatching a native select change. */
export async function selectTaskOption(label: string, option: string) {
  const user = userEvent.setup();
  await user.click(screen.getByLabelText(label));
  await user.click(await screen.findByRole("option", { name: option }));
}
