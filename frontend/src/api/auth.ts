/** 登录/登出/验证码接口。token 的存取见 @/auth/token。 */
import { http } from './http'

export interface Captcha {
  captcha_id: string
  /** PNG 的 data URI，直接给 <img :src> 用 */
  image: string
}

export interface LoginResult {
  token: string
  token_type: string
  expires_in: number
}

export function fetchCaptcha(): Promise<Captcha> {
  return http.get<Captcha>('/api/auth/captcha')
}

export function login(
  username: string,
  password: string,
  captchaId: string,
  captchaCode: string,
): Promise<LoginResult> {
  return http.post<LoginResult>('/api/auth/login', {
    username,
    password,
    captcha_id: captchaId,
    captcha_code: captchaCode,
  })
}

export function logout(): Promise<void> {
  return http.post<void>('/api/auth/logout')
}

export function fetchMe(): Promise<{ username: string }> {
  return http.get<{ username: string }>('/api/auth/me')
}
