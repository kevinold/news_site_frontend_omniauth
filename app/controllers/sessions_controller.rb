class SessionsController < ApplicationController

  def new
    redirect_to '/auth/twitter'
  end

  def create
    auth = request.env["omniauth.auth"]
    #puts "******************** #{params[:oauth_token]}"
    #puts "******************** #{params[:oauth_verifier]}"
    user = User.where(:provider => auth['provider'],
                      :uid => auth['uid'].to_s).first || User.create_with_omniauth(auth)

    #tweet_count = Harvest.where(:uid = user.id)

    #if tweet_count
    Harvest.where(uid: user.uid).delete
    #else
    HarvestWorker.perform_async(user.uid, auth['credentials']['token'], auth['credentials']['secret'])

    #system "python /home/ubuntu/nuztap-harvest/harvest.py #{user.uid} #{auth['credentials']['token']} #{auth['credentials']['secret']}"
    #end

    #puts "******** ret"
    # Reset the session after successful login, per
    # 2.8 Session Fixation – Countermeasures:
    # http://guides.rubyonrails.org/security.html#session-fixation-countermeasures
    reset_session
    session[:user_id] = user.id
    #user.add_role :admin if User.count == 1 # make the first user an admin
    #if user.email.blank?
    #  redirect_to edit_user_path(user), :alert => "Please enter your email address."
    #else
    redirect_to root_url, :notice => 'Signed in!'
    #end

  end

  def destroy
    reset_session
    redirect_to root_url, :notice => 'Signed out!'
  end

  def failure
    redirect_to root_url, :alert => "Authentication error: #{params[:message].humanize}"
  end

end
