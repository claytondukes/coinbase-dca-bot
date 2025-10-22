import schedule
import time
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class scheduleSetup():
    def __init__(self, json_schedule):
        with open(json_schedule) as f:
            self.schedule_data = json.load(f)
    
        self.frequency_list = []
    
    def create_schedule(self, task, exchange):

        choice_dict = {
            'seconds': self._set_seconds,
            'hourly': self._set_hourly,
            'daily': self._set_daily,
            'weekly': self._set_weekly,
            'monthly': self._set_monthly,
            'once': self._set_once
        }

        self.frequency_list.append(task['frequency'])

        chosen_function = choice_dict.get(task['frequency'], self._other_schedule)
        chosen_function(task, exchange)

    def _set_seconds(self, task, exchange_function):
        schedule.every(task['seconds']).seconds.do(exchange_function)
        logger.info('Schedule set: every {} seconds | {} for {} quote currency'.format(
            task['seconds'],
            task['currency_pair'],
            task['quote_currency_amount']
        ))

    def _set_hourly(self, task, exchange_function):
        schedule.every().hour.do(exchange_function)
        logger.info('Schedule set: {} | {} for {} quote currency'.format(
            task['frequency'], 
            task['currency_pair'],
            task['quote_currency_amount']
        ))

    def _set_daily(self, task, exchange_function):
        schedule.every().day.at(task['time']).do(exchange_function)
        logger.info('Schedule set: {} at {} | {} for {} quote currency'.format(
            task['frequency'], 
            task['time'],
            task['currency_pair'],
            task['quote_currency_amount']
        ))

    def _set_weekly(self, task, exchange_function):
        def schedule_job(day, time):
            days = {
                'monday': schedule.every().monday,
                'tuesday': schedule.every().tuesday,
                'wednesday': schedule.every().wednesday,
                'thursday': schedule.every().thursday,
                'friday': schedule.every().friday,
                'saturday': schedule.every().saturday,
                'sunday': schedule.every().sunday
            }
            if day.lower() in days:
                days[day.lower()].at(time).do(exchange_function)
            else:
                logger.error("Invalid day of the week")
        
        schedule_job(task['day_of_week'], task['time'])

        logger.info('Schedule set: {} on {} at {} | {} for {} quote currency'.format(
            task['frequency'],
            task['day_of_week'], 
            task['time'],
            task['currency_pair'],
            task['quote_currency_amount']
        ))
    
    def _set_monthly(self, task, exchange_function):
        def monthly_job():
            today = datetime.today()
            if today.day == task['day_of_month']:
                exchange_function()
        
        schedule.every().day.at(task['time']).do(monthly_job)

        logger.info('Schedule set: {} on day {} at {} | {} for {} quote currency'.format(
            task['frequency'],
            task['day_of_month'], 
            task['time'],
            task['currency_pair'],
            task['quote_currency_amount']
        ))

    def _set_once(self, task, exchange_function):
        job_holder = {}

        def run_once():
            try:
                exchange_function()
            finally:
                try:
                    j = job_holder.get('job')
                    if j:
                        schedule.cancel_job(j)
                except Exception:
                    pass
                logger.info('Once schedule executed and cancelled: {} at {} | {} for {} quote currency'.format(
                    task['frequency'],
                    task['time'],
                    task['currency_pair'],
                    task['quote_currency_amount']
                ))

        job = schedule.every().day.at(task['time']).do(run_once)
        job_holder['job'] = job
        logger.info('Schedule set: {} at {} | {} for {} quote currency'.format(
            task['frequency'], 
            task['time'],
            task['currency_pair'],
            task['quote_currency_amount']
        ))

    def _other_schedule(self, task, exchange_function):
        logger.error('no valid "frequency" key:value in schedule configuration found')

    def show_schedule(self):
        logger.info('Scheduled jobs:')
        for job in schedule.jobs:
            try:
                logger.info(f"{job} | next_run={job.next_run}")
            except Exception:
                logger.info(str(job))

    def start_schedule(self):

        if 'seconds' in self.frequency_list:
            sleep_time = 1
            logger.info('seconds schedules present')
            logger.info('set sleep time to 1 second')
        else:
            sleep_time = 60
            logger.info('no seconds/hourly schedules present')
            logger.info('set sleep time to 60 seconds')
        
        logger.info('*** start schedule ***')
        while True:
            schedule.run_pending()
            time.sleep(sleep_time)