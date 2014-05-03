Rails.application.config.middleware.use OmniAuth::Builder do
  #provider :twitter, ENV['OMNIAUTH_PROVIDER_KEY'], ENV['OMNIAUTH_PROVIDER_SECRET']
  provider :twitter, 'Yazg8rJaUO0CGb2NQxiiTdV8F', 'mFo4uSuQkykNcATJWgWFOevfkqBLSuAWqlCr3riM00xGgSDb3a'
end
