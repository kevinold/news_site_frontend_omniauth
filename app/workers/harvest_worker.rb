class HarvestWorker
  include Sidekiq::Worker
  #sidekiq_options queue: "harvest"

  def perform(uid, token, secret)

    system "python #{Rails.root.join('bin/harvest.py')} #{uid} #{token} #{secret}"
    #system "python /home/ubuntu/nuztap-harvest/harvest.py #{user.uid} #{auth['credentials']['token']} #{auth['credentials']['secret']}"

    #uri = URI.parse("http://pygments.appspot.com/")
    #request = Net::HTTP.post_form(uri, lang: snippet.language, code: snippet.plain_code)
    
  end
end
