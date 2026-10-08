import { Surface } from "./shell/Surface";
interface RegistrationQqGateProps {
  open: boolean;
  onBind: () => void;
  onSkip: () => void;
}
export function RegistrationQqGate({ open, onBind, onSkip }: RegistrationQqGateProps) {
  if (!open) return null;
  return (
    <Surface title="已有 QQ 用户建议先绑定" role="alertdialog" onClose={onSkip}>
      <p>绑定后可继续使用原 QQ 账号的长期记忆。请选择现在绑定，或明确跳过此步骤。</p>
      <button onClick={onSkip}>以后再说</button>
      <button onClick={onBind}>绑定已有 QQ</button>
    </Surface>
  );
}
