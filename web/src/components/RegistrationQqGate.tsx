import { AlertDialog, Button, Flex } from "@radix-ui/themes";

interface RegistrationQqGateProps {
  open: boolean;
  onBind: () => void;
  onSkip: () => void;
}

export function RegistrationQqGate({ open, onBind, onSkip }: RegistrationQqGateProps) {
  return (
    <AlertDialog.Root open={open}>
      <AlertDialog.Content maxWidth="420px">
        <AlertDialog.Title>已有 QQ 用户建议先绑定</AlertDialog.Title>
        <AlertDialog.Description size="2">
          绑定后可继续使用原 QQ 账号的长期记忆。请选择现在绑定，或明确跳过此步骤。
        </AlertDialog.Description>
        <Flex gap="3" mt="4" justify="end">
          <AlertDialog.Cancel>
            <Button variant="soft" color="gray" onClick={onSkip}>
              以后再说
            </Button>
          </AlertDialog.Cancel>
          <AlertDialog.Action>
            <Button onClick={onBind}>绑定已有 QQ</Button>
          </AlertDialog.Action>
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
}
